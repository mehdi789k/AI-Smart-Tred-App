#property strict

#include <Trade/Trade.mqh>
#include "Logger.mqh"
#include "RiskManager.mqh"

struct STradeRequest
  {
   string symbol;
   ENUM_ORDER_TYPE type;
   double volume;
   double price;
   double stop_loss;
   double take_profit;
   string comment;
   long   magic;
  };

struct SExecutionResult
  {
   bool   accepted;
   ulong  order_ticket;
   ulong  deal_ticket;
   string code;
   string message;
  };

class COrderManager
  {
private:
   CTrade *m_trade;
   CTradeLogger *m_log;
   CRiskManager *m_risk;
   int m_deviation_points;

   void Result(SExecutionResult &result,const bool accepted,const string code,
               const string message,const ulong order_ticket=0,const ulong deal_ticket=0)
     {
      result.accepted=accepted; result.code=code; result.message=message;
      result.order_ticket=order_ticket; result.deal_ticket=deal_ticket;
     }
   bool IsMarketType(const ENUM_ORDER_TYPE type)
     {
      return type==ORDER_TYPE_BUY || type==ORDER_TYPE_SELL;
     }

public:
   COrderManager(void)
     {
      m_trade=NULL; m_log=NULL; m_risk=NULL; m_deviation_points=20;
     }

   void Initialize(CTrade &trade,CTradeLogger &logger,CRiskManager &risk,
                   const int deviation_points=20)
     {
      m_trade=&trade; m_log=&logger; m_risk=&risk;
      m_deviation_points=MathMax(0,deviation_points);
      m_trade.SetExpertMagicNumber(m_risk.Magic());
      m_trade.SetDeviationInPoints(m_deviation_points);
      m_trade.SetAsyncMode(false);
     }

   bool OpenMarket(const STradeRequest &request,SExecutionResult &result)
     {
      Result(result,false,"not_initialized","order manager is not initialized");
      if(m_trade==NULL || m_log==NULL || m_risk==NULL) return false;
      if(!IsMarketType(request.type))
        {
         Result(result,false,"unsupported_order_type","only market orders are enabled");
         return false;
        }
      SRiskSignal signal;
      signal.action=request.type==ORDER_TYPE_BUY ? "buy" : "sell";
      signal.symbol=request.symbol; signal.volume=request.volume;
      signal.stop_loss=request.stop_loss; signal.take_profit=request.take_profit;
      signal.risk_score=0; signal.magic=request.magic;
      if(!m_risk.Validate(signal,true))
        {
         Result(result,false,"risk_gate_rejected","risk validation failed");
         m_log.Error("order_rejected","risk gate rejected "+request.symbol);
         return false;
        }
      m_trade.SetExpertMagicNumber(m_risk.Magic());
      bool ok=false;
      if(request.type==ORDER_TYPE_BUY)
         ok=m_trade.Buy(request.volume,request.symbol,0.0,request.stop_loss,
                        request.take_profit,request.comment);
      else
         ok=m_trade.Sell(request.volume,request.symbol,0.0,request.stop_loss,
                         request.take_profit,request.comment);
      if(!ok)
        {
         string msg=StringFormat("retcode=%u %s",(uint)m_trade.ResultRetcode(),
                                 m_trade.ResultRetcodeDescription());
         Result(result,false,"mt5_order_failed",msg);
         m_log.Error("order_open_failed",msg);
         return false;
        }
      Result(result,true,"accepted","market order accepted",
             m_trade.ResultOrder(),m_trade.ResultDeal());
      m_log.Info("order_opened",StringFormat("symbol=%s volume=%.2f order=%I64u deal=%I64u",
                                              request.symbol,request.volume,
                                              m_trade.ResultOrder(),m_trade.ResultDeal()));
      return true;
     }

   bool ModifyPosition(const ulong ticket,const double stop_loss,const double take_profit,
                       SExecutionResult &result)
     {
      Result(result,false,"invalid_request","invalid position request");
      if(m_trade==NULL || m_risk==NULL) return false;
      if(ticket==0 || !PositionSelectByTicket(ticket)) return false;
      if((long)PositionGetInteger(POSITION_MAGIC)!=m_risk.Magic()) return false;
      string symbol=PositionGetString(POSITION_SYMBOL);
      double point=SymbolInfoDouble(symbol,SYMBOL_POINT);
      // Zero means "leave that protection level absent"; negative values
      // are invalid.  This permits trailing/break-even on positions that
      // were opened without a take-profit.
      if(point<=0 || stop_loss<0 || take_profit<0) return false;
      if(!m_trade.PositionModify(ticket,stop_loss,take_profit))
        {
         string msg=StringFormat("retcode=%u %s",(uint)m_trade.ResultRetcode(),
                                 m_trade.ResultRetcodeDescription());
         Result(result,false,"mt5_modify_failed",msg);
         if(m_log!=NULL) m_log.Error("order_modify_failed",msg);
         return false;
        }
      Result(result,true,"modified","position modified",m_trade.ResultOrder(),m_trade.ResultDeal());
      if(m_log!=NULL) m_log.Info("position_modified",StringFormat("ticket=%I64u",ticket));
      return true;
     }

   bool PartialClose(const ulong ticket,const double volume,SExecutionResult &result)
     {
      Result(result,false,"invalid_request","invalid partial close request");
      if(m_trade==NULL || m_risk==NULL || ticket==0 || volume<=0) return false;
      if(!PositionSelectByTicket(ticket) ||
         (long)PositionGetInteger(POSITION_MAGIC)!=m_risk.Magic()) return false;
      double current=PositionGetDouble(POSITION_VOLUME);
      if(volume>=current) return ClosePosition(ticket,result);
      if(!m_trade.PositionClosePartial(ticket,volume))
        {
         string msg=StringFormat("retcode=%u %s",(uint)m_trade.ResultRetcode(),
                                 m_trade.ResultRetcodeDescription());
         Result(result,false,"mt5_partial_close_failed",msg);
         if(m_log!=NULL) m_log.Error("partial_close_failed",msg);
         return false;
        }
      Result(result,true,"partial_closed","partial close accepted",
             m_trade.ResultOrder(),m_trade.ResultDeal());
      if(m_log!=NULL) m_log.Info("partial_closed",StringFormat("ticket=%I64u volume=%.2f",ticket,volume));
      return true;
     }

   bool ClosePosition(const ulong ticket,SExecutionResult &result)
     {
      Result(result,false,"invalid_request","invalid close request");
      if(m_trade==NULL || m_risk==NULL || ticket==0 || !PositionSelectByTicket(ticket) ||
         (long)PositionGetInteger(POSITION_MAGIC)!=m_risk.Magic()) return false;
      if(!m_trade.PositionClose(ticket))
        {
         string msg=StringFormat("retcode=%u %s",(uint)m_trade.ResultRetcode(),
                                 m_trade.ResultRetcodeDescription());
         Result(result,false,"mt5_close_failed",msg);
         if(m_log!=NULL) m_log.Error("close_failed",msg);
         return false;
        }
      Result(result,true,"closed","position closed",m_trade.ResultOrder(),m_trade.ResultDeal());
      if(m_log!=NULL) m_log.Info("position_closed",StringFormat("ticket=%I64u",ticket));
      return true;
     }

   int CloseAll(const string symbol="")
     {
      if(m_trade==NULL || m_risk==NULL) return 0;
      int closed=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
        {
         ulong ticket=PositionGetTicket(i);
         if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
         if((long)PositionGetInteger(POSITION_MAGIC)!=m_risk.Magic()) continue;
         if(symbol!="" && PositionGetString(POSITION_SYMBOL)!=symbol) continue;
         SExecutionResult result;
         if(ClosePosition(ticket,result)) closed++;
        }
      return closed;
     }
  };
