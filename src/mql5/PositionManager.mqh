#property strict

#include <Trade/Trade.mqh>
#include "Logger.mqh"
#include "OrderManager.mqh"
#include "RiskManager.mqh"

class CPositionManager
  {
private:
   CTradeLogger *m_log;
   COrderManager *m_orders;
   CRiskManager *m_risk;
   double m_trailing_points;
   double m_break_even_trigger_points;
   double m_break_even_offset_points;
   int    m_max_minutes;
   double m_partial_trigger_points;
   double m_partial_fraction;
   bool   m_partial_done[];
   ulong  m_partial_tickets[];

   bool WasPartial(const ulong ticket)
     {
      for(int i=0;i<ArraySize(m_partial_tickets);i++)
         if(m_partial_tickets[i]==ticket) return true;
      return false;
     }
   void MarkPartial(const ulong ticket)
     {
      int n=ArraySize(m_partial_tickets);
      ArrayResize(m_partial_tickets,n+1); m_partial_tickets[n]=ticket;
     }

public:
   CPositionManager(void)
     {
      m_log=NULL; m_orders=NULL; m_risk=NULL;
      m_trailing_points=0; m_break_even_trigger_points=0;
      m_break_even_offset_points=0; m_max_minutes=0;
      m_partial_trigger_points=0; m_partial_fraction=0.5;
      ArrayResize(m_partial_tickets,0);
     }

   void Initialize(CTradeLogger &logger,COrderManager &orders,CRiskManager &risk,
                   const double trailing_points,const double break_even_trigger_points,
                   const double break_even_offset_points,const int max_minutes,
                   const double partial_trigger_points,const double partial_fraction)
     {
      m_log=&logger; m_orders=&orders; m_risk=&risk;
      m_trailing_points=MathMax(0.0,trailing_points);
      m_break_even_trigger_points=MathMax(0.0,break_even_trigger_points);
      m_break_even_offset_points=MathMax(0.0,break_even_offset_points);
      m_max_minutes=MathMax(0,max_minutes);
      m_partial_trigger_points=MathMax(0.0,partial_trigger_points);
      m_partial_fraction=MathMin(0.9,MathMax(0.1,partial_fraction));
     }

   void Manage(void)
     {
      if(m_orders==NULL || m_risk==NULL) return;
      for(int i=PositionsTotal()-1;i>=0;i--)
        {
         ulong ticket=PositionGetTicket(i);
         if(ticket==0 || !PositionSelectByTicket(ticket)) continue;
         if((long)PositionGetInteger(POSITION_MAGIC)!=m_risk.Magic()) continue;
         ManagePosition(ticket);
        }
     }

   void ManagePosition(const ulong ticket)
     {
      if(!PositionSelectByTicket(ticket)) return;
      string symbol=PositionGetString(POSITION_SYMBOL);
      ENUM_POSITION_TYPE type=(ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double open=PositionGetDouble(POSITION_PRICE_OPEN);
      double sl=PositionGetDouble(POSITION_SL);
      double tp=PositionGetDouble(POSITION_TP);
      double volume=PositionGetDouble(POSITION_VOLUME);
      double point=SymbolInfoDouble(symbol,SYMBOL_POINT);
      int digits=(int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
      if(point<=0 || digits<0) return;
      double bid=SymbolInfoDouble(symbol,SYMBOL_BID);
      double ask=SymbolInfoDouble(symbol,SYMBOL_ASK);
      double current=type==POSITION_TYPE_BUY ? bid : ask;
      double profit_points=type==POSITION_TYPE_BUY ? (current-open)/point : (open-current)/point;
      if(m_max_minutes>0)
        {
         datetime opened=(datetime)PositionGetInteger(POSITION_TIME);
         if(opened>0 && (TimeCurrent()-opened)>=m_max_minutes*60)
           {
            SExecutionResult closed;
            m_orders.ClosePosition(ticket,closed);
            return;
           }
        }
      if(m_partial_trigger_points>0 && profit_points>=m_partial_trigger_points &&
         !WasPartial(ticket) && volume>0)
        {
         SExecutionResult partial;
         if(m_orders.PartialClose(ticket,NormalizeDouble(volume*m_partial_fraction,2),partial))
            MarkPartial(ticket);
        }
      double new_sl=sl;
      if(m_break_even_trigger_points>0 && profit_points>=m_break_even_trigger_points)
        {
         double be=type==POSITION_TYPE_BUY ? open+m_break_even_offset_points*point :
                                             open-m_break_even_offset_points*point;
         if(type==POSITION_TYPE_BUY && (sl==0 || be>sl)) new_sl=be;
         if(type==POSITION_TYPE_SELL && (sl==0 || be<sl)) new_sl=be;
        }
      if(m_trailing_points>0 && profit_points>=m_trailing_points)
        {
         double trail=type==POSITION_TYPE_BUY ? current-m_trailing_points*point :
                                                current+m_trailing_points*point;
         if(type==POSITION_TYPE_BUY && (new_sl==0 || trail>new_sl)) new_sl=trail;
         if(type==POSITION_TYPE_SELL && (new_sl==0 || trail<new_sl)) new_sl=trail;
        }
      new_sl=NormalizeDouble(new_sl,digits);
      if(new_sl<=0 || new_sl==sl) return;
      // Never move a stop in the wrong direction or across the current price.
      if(type==POSITION_TYPE_BUY && new_sl>=bid) return;
      if(type==POSITION_TYPE_SELL && new_sl<=ask) return;
      SExecutionResult modified;
      m_orders.ModifyPosition(ticket,new_sl,tp,modified);
     }
  };
