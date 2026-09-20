#property strict
#property version "1.0"
#property description "Compile-time and fail-closed smoke tests for SmartTraderEA modules."

#include "../../src/mql5/Logger.mqh"
#include "../../src/mql5/ZmqClient.mqh"
#include "../../src/mql5/RiskManager.mqh"
#include "../../src/mql5/OrderManager.mqh"

CTradeLogger g_test_log;
CZeroMqClient g_test_zmq;
CRiskManager g_test_risk;
int g_failures=0;

void Check(const bool condition,const string name)
  {
   if(condition) PrintFormat("[PASS] %s",name);
   else { PrintFormat("[FAIL] %s",name); g_failures++; }
  }

int OnInit(void)
  {
   g_test_log.Configure("TestEA",true);
   g_test_zmq.Configure(g_test_log,"tcp://127.0.0.1:5555",100);
#ifndef SMARTTRADER_ENABLE_ZMQ_DLL
   Check(!g_test_zmq.Connect(),"zmq disabled or unavailable fails closed");
#else
   Check(true,"zmq DLL path is opt-in");
#endif
   SEnvelope envelope;
   Check(!g_test_zmq.ParseEnvelope("{}",envelope),"malformed envelope rejected");
   Check(g_test_risk.Initialize(g_test_log,"XAUUSD,EURUSD",1234,0.10,100,10.0),
         "valid risk configuration accepted");
   Check(!g_test_risk.IsTripped(),"circuit breaker starts clear");
   g_test_risk.Trip("test");
   Check(g_test_risk.IsTripped(),"circuit breaker trips");
   Check(!g_test_risk.Reset("x"),"short reset confirmation rejected");
   Check(g_test_risk.Reset("test-confirmation"),"valid reset confirmation accepted");
   Check(!g_test_risk.IsTripped(),"circuit breaker resets");
   PrintFormat("TestEA completed failures=%d",g_failures);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   if(g_failures>0) PrintFormat("TestEA failed failures=%d",g_failures);
  }
