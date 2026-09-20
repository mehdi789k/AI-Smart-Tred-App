#property strict

// Structured, secret-free logger used by the EA.  Messages are deliberately
// kept small: the terminal journal is not a secure audit store.
enum ENUM_ST_LOG_LEVEL
  {
   ST_LOG_INFO=0,
   ST_LOG_WARN=1,
   ST_LOG_ERROR=2,
   ST_LOG_DEBUG=3
  };

class CTradeLogger
  {
private:
   string m_component;
   bool   m_debug;

   string Safe(const string value)
     {
      string result=value;
      StringReplace(result,"password","[redacted]");
      StringReplace(result,"passwd","[redacted]");
      StringReplace(result,"token","[redacted]");
      StringReplace(result,"secret","[redacted]");
      StringReplace(result,"api_key","[redacted]");
      StringReplace(result,"authorization","[redacted]");
      StringReplace(result,"endpoint","[redacted]");
      return result;
     }

public:
   CTradeLogger(void)
     {
      m_component="SmartTrader";
      m_debug=false;
     }

   void Configure(const string component,const bool debug=false)
     {
      m_component=component;
      m_debug=debug;
     }

   void Write(const ENUM_ST_LOG_LEVEL level,const string event_name,
              const string message="",const long request_id=0)
     {
      if(level==ST_LOG_DEBUG && !m_debug)
         return;
      string level_name="INFO";
      if(level==ST_LOG_WARN)  level_name="WARN";
      if(level==ST_LOG_ERROR) level_name="ERROR";
      if(level==ST_LOG_DEBUG) level_name="DEBUG";
      string suffix="";
      if(request_id!=0)
         suffix=StringFormat(" request_id=%I64d",request_id);
      PrintFormat("[%s] component=%s event=%s%s message=%s",
                  level_name,m_component,event_name,suffix,Safe(message));
     }

   void Info(const string event_name,const string message="")
     {
      Write(ST_LOG_INFO,event_name,message);
     }
   void Warn(const string event_name,const string message="")
     {
      Write(ST_LOG_WARN,event_name,message);
     }
   void Error(const string event_name,const string message="")
     {
      Write(ST_LOG_ERROR,event_name,message);
     }
   void Debug(const string event_name,const string message="")
     {
      Write(ST_LOG_DEBUG,event_name,message);
     }
  };
