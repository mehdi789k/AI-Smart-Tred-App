#property strict

#include "Logger.mqh"

struct SRiskSignal
  {
   string action;
   string symbol;
   double volume;
   double stop_loss;
   double take_profit;
   double risk_score;
   long   magic;
  };

class CRiskManager
  {
private:
   CTradeLogger *m_log;
   string m_whitelist[];
   long   m_magic;
   double m_max_lot;
   int    m_max_spread_points;
   double m_daily_loss_limit;
   double m_daily_loss;
   datetime m_day;
   bool   m_tripped;

   bool HasSymbol(const string symbol)
     {
      for(int i=0;i<ArraySize(m_whitelist);i++)
         if(m_whitelist[i]==symbol) return true;
      return false;
     }
   void StartNewDay(void)
     {
      datetime now=TimeCurrent();
      MqlDateTime dt; TimeToStruct(now,dt); dt.hour=0; dt.min=0; dt.sec=0;
      m_day=StructToTime(dt);
      m_daily_loss=0.0;
     }
   void RebuildDailyLoss(void)
     {
      if(m_day<=0 || !HistorySelect(m_day,TimeCurrent())) return;
      double total=0.0;
      int deals=HistoryDealsTotal();
      for(int i=0;i<deals;i++)
        {
         ulong deal=HistoryDealGetTicket(i);
         if(deal==0) continue;
         if(HistoryDealGetInteger(deal,DEAL_MAGIC)!=m_magic) continue;
         long entry=HistoryDealGetInteger(deal,DEAL_ENTRY);
         if(entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY) continue;
         double pnl=HistoryDealGetDouble(deal,DEAL_PROFIT);
         pnl+=HistoryDealGetDouble(deal,DEAL_COMMISSION);
         pnl+=HistoryDealGetDouble(deal,DEAL_SWAP);
         if(pnl<0) total+=-pnl;
        }
      m_daily_loss=total;
      if(m_daily_loss_limit>0 && m_daily_loss>=m_daily_loss_limit)
         m_tripped=true;
     }
   string Trim(const string source)
     {
      string value=source;
      StringTrimLeft(value);
      StringTrimRight(value);
      return value;
     }

public:
   CRiskManager(void)
     {
      m_log=NULL; m_magic=0; m_max_lot=0; m_max_spread_points=0;
      m_daily_loss_limit=0; m_daily_loss=0; m_day=0; m_tripped=false;
     }

   bool Initialize(CTradeLogger &logger,const string whitelist,const long magic,
                   const double max_lot,const int max_spread_points,
                   const double daily_loss_limit)
     {
      m_log=&logger; m_magic=magic; m_max_lot=max_lot;
      m_max_spread_points=max_spread_points;
      m_daily_loss_limit=MathMax(0.0,daily_loss_limit);
      ArrayResize(m_whitelist,0);
      string parts[]; int count=StringSplit(whitelist,',',parts);
      for(int i=0;i<count;i++)
        {
         string item=Trim(parts[i]);
         if(StringLen(item)>0)
           {
            int n=ArraySize(m_whitelist);
            ArrayResize(m_whitelist,n+1); m_whitelist[n]=item;
           }
        }
      StartNewDay();
      RebuildDailyLoss();
      if(m_magic<=0 || m_max_lot<=0 || ArraySize(m_whitelist)==0)
        {
         if(m_log!=NULL) m_log.Error("risk_config","invalid fail-closed configuration");
         return false;
        }
      return true;
     }

   void Refresh(void)
     {
      datetime now=TimeCurrent();
      MqlDateTime dt; TimeToStruct(now,dt); dt.hour=0; dt.min=0; dt.sec=0;
      datetime today=StructToTime(dt);
      if(today!=m_day)
        {
         StartNewDay();
         RebuildDailyLoss();
        }
      if(m_daily_loss_limit>0 && m_daily_loss>=m_daily_loss_limit)
         m_tripped=true;
     }

   void RecordClosedProfit(const double profit)
     {
      Refresh();
      if(profit<0) m_daily_loss+=-profit;
      if(m_daily_loss_limit>0 && m_daily_loss>=m_daily_loss_limit)
        {
         m_tripped=true;
         if(m_log!=NULL) m_log.Error("circuit_breaker","daily loss limit reached");
        }
     }

   bool IsTradingAllowed(void)
     {
      Refresh();
      return !m_tripped;
     }
   void Trip(const string reason)
     {
      m_tripped=true;
      if(m_log!=NULL) m_log.Error("circuit_breaker","tripped: "+reason);
     }
   bool Reset(const string confirmation)
     {
      if(StringLen(confirmation)<8) return false;
      Refresh();
      // A manual reset never erases the audited daily loss.  It can only
      // clear an operator trip while the configured daily limit is not
      // currently breached.
      if(m_daily_loss_limit>0 && m_daily_loss>=m_daily_loss_limit)
        {
         if(m_log!=NULL) m_log.Error("circuit_breaker","reset refused: daily loss limit remains breached");
         return false;
        }
      m_tripped=false;
      if(m_log!=NULL) m_log.Warn("circuit_breaker","manual reset accepted");
      return true;
     }
   bool IsTripped(void) { Refresh(); return m_tripped; }
   double DailyLoss(void) { Refresh(); return m_daily_loss; }
   long Magic(void) { return m_magic; }

   bool Validate(const SRiskSignal &signal,const bool require_stops=true)
     {
      Refresh();
      if(m_tripped) return false;
      if(!HasSymbol(signal.symbol)) return false;
      if(signal.magic!=0 && signal.magic!=m_magic) return false;
      if(signal.volume<=0 || signal.volume>m_max_lot) return false;
      if(!SymbolSelect(signal.symbol,true)) return false;
      long spread=(long)SymbolInfoInteger(signal.symbol,SYMBOL_SPREAD);
      if(m_max_spread_points>0 && spread>m_max_spread_points) return false;
      double point=SymbolInfoDouble(signal.symbol,SYMBOL_POINT);
      int digits=(int)SymbolInfoInteger(signal.symbol,SYMBOL_DIGITS);
      if(point<=0 || digits<0) return false;
      if(require_stops && (signal.stop_loss<=0 || signal.take_profit<=0)) return false;
      if(signal.stop_loss>0 && NormalizeDouble(signal.stop_loss,digits)<=0) return false;
      if(signal.take_profit>0 && NormalizeDouble(signal.take_profit,digits)<=0) return false;
      double bid=SymbolInfoDouble(signal.symbol,SYMBOL_BID);
      double ask=SymbolInfoDouble(signal.symbol,SYMBOL_ASK);
      long stops_level=(long)SymbolInfoInteger(signal.symbol,SYMBOL_TRADE_STOPS_LEVEL);
      double minimum_distance=MathMax(point,(double)stops_level*point);
      string action=signal.action;
      StringToLower(action);
      if(action=="buy")
        {
         if(signal.stop_loss>=ask-minimum_distance || signal.take_profit<=ask+minimum_distance)
            return false;
        }
      else if(action=="sell")
        {
         if(signal.stop_loss<=bid+minimum_distance || signal.take_profit>=bid-minimum_distance)
            return false;
        }
      else
         return false;
      double volume_min=SymbolInfoDouble(signal.symbol,SYMBOL_VOLUME_MIN);
      double volume_step=SymbolInfoDouble(signal.symbol,SYMBOL_VOLUME_STEP);
      double volume_max=SymbolInfoDouble(signal.symbol,SYMBOL_VOLUME_MAX);
      if(volume_min<=0 || volume_step<=0 || volume_max<=0 ||
         signal.volume<volume_min || signal.volume>volume_max)
         return false;
      double steps=MathFloor(signal.volume/volume_step+1e-9);
      if(steps<=0 || MathAbs(steps*volume_step-signal.volume)>volume_step*0.001)
         return false;
      return true;
     }

   bool ValidateSymbol(const string symbol)
     {
      Refresh();
      if(!HasSymbol(symbol) || !SymbolSelect(symbol,true)) return false;
      long spread=(long)SymbolInfoInteger(symbol,SYMBOL_SPREAD);
      return m_max_spread_points<=0 || spread<=m_max_spread_points;
     }
  };
