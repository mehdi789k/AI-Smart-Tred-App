#property strict
#property version   "1.0"
#property description "Fail-closed MT5 execution adapter for the versioned signal envelope."

#include "ExecutionPolicy.mqh"
#include "Logger.mqh"
#include "ZmqClient.mqh"
#include "RiskManager.mqh"
#include "OrderManager.mqh"
#include "PositionManager.mqh"

input bool   InpEnableZmq              = false;
input string InpZmqEndpoint            = "tcp://127.0.0.1:5555";
input int    InpZmqTimeoutMs           = 1000;
input int    InpPollMilliseconds       = 250;
input string InpSymbolWhitelist        = EXECUTION_POLICY_WHITELIST;
// Must match MT5_LIVE_MAGIC / LiveOrderConfig.magic in the Python execution
// workflow.  Keep this fail-closed default aligned with the server contract;
// operators may still override it explicitly per terminal/account.
input long   InpMagicNumber            = EXECUTION_POLICY_MAGIC_NUMBER;
input double InpMaxLotSize             = EXECUTION_POLICY_MAX_LOT;
input int    InpMaxSpreadPoints        = 80;
input double InpDailyLossLimitAccount  = EXECUTION_POLICY_DAILY_LOSS;
input int    InpDeviationPoints        = 20;
input bool   InpAllowLiveTrading       = EXECUTION_POLICY_LIVE_ENABLED;
input double InpTrailingStopPoints     = 0.0;
input double InpBreakEvenTriggerPoints = 0.0;
input double InpBreakEvenOffsetPoints  = 0.0;
input int    InpMaxPositionMinutes     = 0;
input double InpPartialTriggerPoints   = 0.0;
input double InpPartialCloseFraction   = 0.50;
input bool   InpDebugLogging           = false;

CTradeLogger     g_log;
CZeroMqClient    g_zmq;
CRiskManager     g_risk;
CTrade           g_trade;
COrderManager    g_orders;
CPositionManager g_positions;
string           g_message_ids[];
bool             g_ready=false;

bool SeenMessage(const string id)
  {
   for(int i=0;i<ArraySize(g_message_ids);i++)
      if(g_message_ids[i]==id) return true;
   return false;
  }

void RememberMessage(const string id)
  {
   int n=ArraySize(g_message_ids);
   if(n>=256)
     {
      for(int i=1;i<n;i++) g_message_ids[i-1]=g_message_ids[i];
      n--;
     }
   ArrayResize(g_message_ids,n+1);
   g_message_ids[n]=id;
  }

string Lower(const string source)
  {
   string result=source;
   StringToLower(result);
   return result;
  }

bool ParseSignal(const SEnvelope &envelope,STradeRequest &request,string &action,
                 long &ticket,string &error)
  {
   request.symbol="";
   request.volume=0;
   request.price=0;
   request.stop_loss=0;
   request.take_profit=0;
   request.comment="SmartTrader";
   request.magic=0;
   request.type=ORDER_TYPE_BUY;
   ticket=0;
   if(!g_zmq.GetPayloadString(envelope.payload,"action",action))
     {
      error="missing action"; return false;
     }
   action=Lower(action);
   if(!g_zmq.GetPayloadString(envelope.payload,"symbol",request.symbol) ||
      StringLen(request.symbol)==0)
     {
      error="missing symbol"; return false;
     }
   g_zmq.GetPayloadDouble(envelope.payload,"volume",request.volume);
   g_zmq.GetPayloadDouble(envelope.payload,"entry",request.price);
   g_zmq.GetPayloadDouble(envelope.payload,"stop_loss",request.stop_loss);
   g_zmq.GetPayloadDouble(envelope.payload,"take_profit",request.take_profit);
   g_zmq.GetPayloadLong(envelope.payload,"magic",request.magic);
   g_zmq.GetPayloadLong(envelope.payload,"ticket",ticket);
   if(request.magic==0) request.magic=g_risk.Magic();
   string comment="";
   if(g_zmq.GetPayloadString(envelope.payload,"signal_id",comment) && StringLen(comment)>0)
      request.comment="signal:"+comment;
   if(action=="buy") request.type=ORDER_TYPE_BUY;
   else if(action=="sell") request.type=ORDER_TYPE_SELL;
   else if(action=="close" || action=="close_all" || action=="hold" ||
           action=="no_action") return true;
   else { error="unsupported action"; return false; }
   return true;
  }

string ProcessMessage(const string raw)
  {
   SEnvelope envelope;
   if(!g_zmq.ParseEnvelope(raw,envelope))
     {
      g_log.Error("message_rejected","invalid JSON envelope");
      // A REP socket must always answer a received request or it becomes
      // unusable for the next request.  Do not echo untrusted input.
      return g_zmq.MakeProtocolError("invalid_envelope","request envelope rejected");
     }
   if(envelope.source!="python" ||
      (envelope.message_type!="signal" && envelope.message_type!="order_request" &&
       envelope.message_type!="heartbeat"))
     {
      g_log.Warn("message_rejected","unknown source or message_type");
      return g_zmq.MakeResult(envelope,false,"invalid_message_type",
                              "unsupported python message_type");
     }
   if(envelope.message_type=="heartbeat")
      return g_zmq.MakeResult(envelope,true,"heartbeat","alive");
   if(SeenMessage(envelope.message_id))
      return g_zmq.MakeResult(envelope,false,"duplicate_message","message already processed");
   RememberMessage(envelope.message_id);

   STradeRequest request;
   string action="",error="";
   long requested_ticket=0;
   if(!ParseSignal(envelope,request,action,requested_ticket,error))
      return g_zmq.MakeResult(envelope,false,"invalid_payload",error);
   if(action=="hold" || action=="no_action")
      return g_zmq.MakeResult(envelope,false,"no_action","signal does not authorize execution");
   if(!g_risk.IsTradingAllowed() && action!="close" && action!="close_all")
      return g_zmq.MakeResult(envelope,false,"circuit_breaker","trading is disabled");
   if(!g_risk.ValidateSymbol(request.symbol))
      return g_zmq.MakeResult(envelope,false,"symbol_or_spread_gate","symbol or spread rejected");

   if(!InpAllowLiveTrading)
      return g_zmq.MakeResult(envelope,false,"dry_run","live trading is disabled");

   if(action=="close" || action=="close_all")
     {
      int count=0;
      if(requested_ticket>0)
        {
         SExecutionResult close_result;
         count=g_orders.ClosePosition((ulong)requested_ticket,close_result) ? 1 : 0;
        }
      else
         count=g_orders.CloseAll(request.symbol);
      if(count<=0) return g_zmq.MakeResult(envelope,false,"close_failed","no matching position was closed");
      return g_zmq.MakeResult(envelope,true,"closed","position close accepted",requested_ticket);
     }

   SExecutionResult result;
   if(!g_orders.OpenMarket(request,result))
      return g_zmq.MakeResult(envelope,false,result.code,result.message,(long)result.order_ticket);
   return g_zmq.MakeResult(envelope,true,result.code,result.message,(long)result.order_ticket);
  }

void Poll(void)
  {
   if(!InpEnableZmq || !g_ready) return;
   if(!g_zmq.IsConnected() && !g_zmq.Connect()) return;
   string raw="";
   if(!g_zmq.Receive(raw)) return;
   string response=ProcessMessage(raw);
   if(response!="" && !g_zmq.Reply(response))
      g_log.Error("zmq_reply_failed","execution result could not be returned");
  }

int OnInit(void)
  {
   g_log.Configure("SmartTraderEA",InpDebugLogging);
   if(!g_risk.Initialize(g_log,InpSymbolWhitelist,InpMagicNumber,InpMaxLotSize,
                         InpMaxSpreadPoints,InpDailyLossLimitAccount))
      return INIT_PARAMETERS_INCORRECT;
   g_orders.Initialize(g_trade,g_log,g_risk,InpDeviationPoints);
   g_positions.Initialize(g_log,g_orders,g_risk,InpTrailingStopPoints,
                         InpBreakEvenTriggerPoints,InpBreakEvenOffsetPoints,
                         InpMaxPositionMinutes,InpPartialTriggerPoints,
                         InpPartialCloseFraction);
   g_zmq.Configure(g_log,InpZmqEndpoint,InpZmqTimeoutMs);
   ArrayResize(g_message_ids,0);
   g_ready=true;
   EventSetMillisecondTimer(MathMax(50,InpPollMilliseconds));
   g_log.Info("ea_initialized",StringFormat("magic=%I64d live=%s",
                                             InpMagicNumber,
                                             InpAllowLiveTrading ? "true" : "false"));
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   g_zmq.Disconnect();
   g_ready=false;
   g_log.Info("ea_deinitialized",StringFormat("reason=%d",reason));
  }

void OnTick(void)
  {
   if(g_ready) g_positions.Manage();
  }

void OnTimer(void)
  {
   if(g_ready)
     {
      g_positions.Manage();
      Poll();
     }
  }

void OnTradeTransaction(const MqlTradeTransaction &trans,
                        const MqlTradeRequest &request,
                        const MqlTradeResult &result)
  {
   if(!g_ready || trans.deal==0) return;
   if(!HistoryDealSelect(trans.deal)) return;
   long magic=HistoryDealGetInteger(trans.deal,DEAL_MAGIC);
   long entry=HistoryDealGetInteger(trans.deal,DEAL_ENTRY);
   if(magic!=g_risk.Magic() ||
      (entry!=DEAL_ENTRY_OUT && entry!=DEAL_ENTRY_OUT_BY)) return;
   double profit=HistoryDealGetDouble(trans.deal,DEAL_PROFIT);
   profit+=HistoryDealGetDouble(trans.deal,DEAL_COMMISSION);
   profit+=HistoryDealGetDouble(trans.deal,DEAL_SWAP);
   g_risk.RecordClosedProfit(profit);
  }
