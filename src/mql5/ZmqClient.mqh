#property strict

#include "Logger.mqh"

// The terminal does not ship ZeroMQ.  This small adapter isolates the optional
// libzmq DLL ABI from the EA.  Compile with SMARTTRADER_ENABLE_ZMQ_DLL and put
// a trusted libzmq.dll in the terminal Libraries directory to enable it.
// Without that define every operation fails closed and the EA remains safe.
#ifdef SMARTTRADER_ENABLE_ZMQ_DLL
#import "libzmq.dll"
long zmq_ctx_new();
long zmq_socket(const long context,const int socket_type);
int  zmq_close(const long socket);
int  zmq_ctx_term(const long context);
int  zmq_connect(const long socket,const string endpoint);
int  zmq_send(const long socket,uchar &buffer[],const int length,const int flags);
int  zmq_recv(const long socket,uchar &buffer[],const int length,const int flags);
int  zmq_setsockopt(const long socket,const int option,const uchar &value[],const int length);
#import
#endif

#define ST_ZMQ_REP 4
#define ST_ZMQ_DONTWAIT 1
#define ST_ZMQ_RCVTIMEO 27
#define ST_ZMQ_SNDTIMEO 28

struct SEnvelope
  {
   string schema_version;
   string message_id;
   string message_type;
   string sent_at;
   string correlation_id;
   string source;
   string payload;
  };

class CZeroMqClient
  {
private:
   CTradeLogger *m_log;
   string m_endpoint;
   int    m_timeout_ms;
   bool   m_connected;
   long   m_context;
   long   m_socket;

   bool JsonString(const string json,const string key,string &value)
     {
      string needle="\""+key+"\"";
      int p=StringFind(json,needle);
      if(p<0) return false;
      p=StringFind(json,":",p+StringLen(needle));
      if(p<0) return false;
      p++;
      while(p<StringLen(json) && (StringGetCharacter(json,p)==' ' ||
            StringGetCharacter(json,p)=='\t' || StringGetCharacter(json,p)=='\r' ||
            StringGetCharacter(json,p)=='\n')) p++;
      if(p>=StringLen(json) || StringGetCharacter(json,p)!='"') return false;
      p++;
      string out="";
      bool escaped=false;
      for(int i=p;i<StringLen(json);i++)
        {
         ushort c=StringGetCharacter(json,i);
         if(escaped)
           {
            if(c=='n') out+="\n";
            else if(c=='r') out+="\r";
            else if(c=='t') out+="\t";
            else out+=ShortToString((short)c);
            escaped=false;
           }
         else if(c=='\\') escaped=true;
         else if(c=='"') { value=out; return true; }
         else out+=ShortToString((short)c);
        }
      return false;
     }

   bool JsonRaw(const string json,const string key,string &value)
     {
      string needle="\""+key+"\"";
      int p=StringFind(json,needle);
      if(p<0) return false;
      p=StringFind(json,":",p+StringLen(needle));
      if(p<0) return false;
      p++;
      while(p<StringLen(json) && StringGetCharacter(json,p)<=32) p++;
      if(p>=StringLen(json)) return false;
      int start=p;
      if(StringGetCharacter(json,p)=='{')
        {
         int depth=0; bool quoted=false; bool escaped=false;
         for(int i=p;i<StringLen(json);i++)
           {
            ushort c=StringGetCharacter(json,i);
            if(quoted)
              {
               if(escaped) escaped=false;
               else if(c=='\\') escaped=true;
               else if(c=='"') quoted=false;
              }
            else
              {
               if(c=='"') quoted=true;
               else if(c=='{') depth++;
               else if(c=='}' && --depth==0) { value=StringSubstr(json,start,i-start+1); return true; }
              }
           }
        }
      else if(StringGetCharacter(json,p)=='[')
        {
         int depth=0; bool quoted=false; bool escaped=false;
         for(int i=p;i<StringLen(json);i++)
           {
            ushort c=StringGetCharacter(json,i);
            if(quoted)
              {
               if(escaped) escaped=false;
               else if(c=='\\') escaped=true;
               else if(c=='"') quoted=false;
              }
            else
              {
               if(c=='"') quoted=true;
               else if(c=='[') depth++;
               else if(c==']' && --depth==0) { value=StringSubstr(json,start,i-start+1); return true; }
              }
           }
        }
      else
        {
         int end=p;
         while(end<StringLen(json) && StringGetCharacter(json,end)!=',' &&
               StringGetCharacter(json,end)!='}') end++;
         value=StringSubstr(json,start,end-start);
         StringTrimRight(value);
         return StringLen(value)>0;
        }
      return false;
     }

   string JsonEscape(const string value)
     {
      string out=value;
      StringReplace(out,"\\","\\\\");
      StringReplace(out,"\"","\\\"");
      StringReplace(out,"\r","\\r");
      StringReplace(out,"\n","\\n");
      return out;
     }
   void NativeIntBytes(const int value,uchar &bytes[])
     {
      ArrayResize(bytes,4);
      bytes[0]=(uchar)(value & 0xff);
      bytes[1]=(uchar)((value >> 8) & 0xff);
      bytes[2]=(uchar)((value >> 16) & 0xff);
      bytes[3]=(uchar)((value >> 24) & 0xff);
     }
   string UtcNow(void)
     {
      string value=TimeToString(TimeGMT(),TIME_DATE|TIME_SECONDS);
      StringReplace(value," ","T");
      return value+"Z";
     }

public:
   CZeroMqClient(void)
     {
      m_log=NULL;
      m_endpoint="";
      m_timeout_ms=1000;
      m_connected=false;
      m_context=0;
      m_socket=0;
     }

   void Configure(CTradeLogger &logger,const string endpoint,const int timeout_ms)
     {
      m_log=&logger;
      m_endpoint=endpoint;
      m_timeout_ms=MathMax(100,timeout_ms);
     }

   bool Connect(void)
     {
      if(m_connected) return true;
      if(StringLen(m_endpoint)<5)
        {
         if(m_log!=NULL) m_log.Error("zmq_connect","invalid endpoint");
         return false;
        }
#ifdef SMARTTRADER_ENABLE_ZMQ_DLL
      m_context=zmq_ctx_new();
      if(m_context==0) return false;
      // Python owns the REQ side and sends a signal first.  The EA is the
      // REP side so it can receive unsolicited signal requests safely.
      m_socket=zmq_socket(m_context,ST_ZMQ_REP);
      if(m_socket==0) { zmq_ctx_term(m_context); m_context=0; return false; }
      uchar timeout[];
      NativeIntBytes(m_timeout_ms,timeout);
      zmq_setsockopt(m_socket,ST_ZMQ_RCVTIMEO,timeout,ArraySize(timeout));
      zmq_setsockopt(m_socket,ST_ZMQ_SNDTIMEO,timeout,ArraySize(timeout));
      if(zmq_connect(m_socket,m_endpoint)!=0)
        {
         zmq_close(m_socket); zmq_ctx_term(m_context);
         m_socket=0; m_context=0;
         if(m_log!=NULL) m_log.Error("zmq_connect","connect failed");
         return false;
        }
      m_connected=true;
      if(m_log!=NULL) m_log.Info("zmq_connected","endpoint="+m_endpoint);
      return true;
#else
      if(m_log!=NULL) m_log.Error("zmq_disabled","compile-time DLL support is disabled");
      return false;
#endif
     }

   void Disconnect(void)
     {
#ifdef SMARTTRADER_ENABLE_ZMQ_DLL
      if(m_socket!=0) zmq_close(m_socket);
      if(m_context!=0) zmq_ctx_term(m_context);
#endif
      m_socket=0; m_context=0; m_connected=false;
     }

   bool IsConnected(void) { return m_connected; }

   bool Receive(string &json)
     {
      json="";
      if(!m_connected) return false;
#ifdef SMARTTRADER_ENABLE_ZMQ_DLL
      uchar buffer[];
      ArrayResize(buffer,65536);
      int received=zmq_recv(m_socket,buffer,ArraySize(buffer),ST_ZMQ_DONTWAIT);
      if(received<=0) return false;
      json=CharArrayToString(buffer,0,received,CP_UTF8);
      return StringLen(json)>0;
#else
      return false;
#endif
     }

   bool Reply(const string json)
     {
      if(!m_connected) return false;
#ifdef SMARTTRADER_ENABLE_ZMQ_DLL
      uchar buffer[];
      int n=StringToCharArray(json,buffer,0,WHOLE_ARRAY,CP_UTF8);
      if(n>0 && buffer[n-1]==0) ArrayResize(buffer,n-1);
      int sent=zmq_send(m_socket,buffer,ArraySize(buffer),0);
      return sent==ArraySize(buffer);
#else
      return false;
#endif
     }

   bool ParseEnvelope(const string json,SEnvelope &envelope)
     {
      envelope.schema_version="";
      envelope.message_id="";
      envelope.message_type="";
      envelope.sent_at="";
      envelope.correlation_id="";
      envelope.source="";
      envelope.payload="";
      if(StringLen(json)<2 || !JsonString(json,"schema_version",envelope.schema_version) ||
         !JsonString(json,"message_id",envelope.message_id) ||
         !JsonString(json,"message_type",envelope.message_type) ||
         !JsonString(json,"sent_at",envelope.sent_at) ||
         !JsonString(json,"correlation_id",envelope.correlation_id) ||
         !JsonString(json,"source",envelope.source) ||
         !JsonRaw(json,"payload",envelope.payload))
         return false;
      return envelope.schema_version=="1.0" && StringLen(envelope.message_id)>0 &&
             StringLen(envelope.correlation_id)>0 && StringLen(envelope.payload)>1;
     }

   bool GetPayloadString(const string payload,const string key,string &value)
     {
      return JsonString(payload,key,value);
     }
   bool GetPayloadDouble(const string payload,const string key,double &value)
     {
      string raw="";
      if(!JsonRaw(payload,key,raw)) return false;
      value=StringToDouble(raw);
      return MathIsValidNumber(value);
     }
   bool GetPayloadLong(const string payload,const string key,long &value)
     {
      string raw="";
      if(!JsonRaw(payload,key,raw)) return false;
      value=(long)StringToInteger(raw);
      return true;
     }
   bool GetPayloadBool(const string payload,const string key,bool &value)
     {
      string raw="";
      if(!JsonRaw(payload,key,raw)) return false;
      if(raw=="true") { value=true; return true; }
      if(raw=="false") { value=false; return true; }
      return false;
     }

   string MakeResult(const SEnvelope &request,const bool accepted,
                     const string code,const string message,const long ticket=0)
     {
      string status=accepted ? "accepted" : "rejected";
      string result_id=StringFormat("mt5-%u",(uint)GetTickCount());
      return StringFormat("{\"schema_version\":\"1.0\",\"message_id\":\"%s\","
                          "\"message_type\":\"order_result\",\"sent_at\":\"%s\","
                          "\"correlation_id\":\"%s\",\"source\":\"mt5\","
                          "\"payload\":{\"status\":\"%s\",\"accepted\":%s,\"code\":\"%s\","
                          "\"message\":\"%s\",\"ticket\":%I64d}}",
                          result_id,UtcNow(),JsonEscape(request.correlation_id),
                          status,accepted ? "true" : "false",JsonEscape(code),
                          JsonEscape(message),ticket);
     }

   string MakeProtocolError(const string code,const string message)
     {
      string result_id=StringFormat("mt5-%u",(uint)GetTickCount());
      return StringFormat("{\"schema_version\":\"1.0\",\"message_id\":\"%s\","
                          "\"message_type\":\"order_result\",\"sent_at\":\"%s\","
                          "\"correlation_id\":\"unknown\",\"source\":\"mt5\","
                          "\"payload\":{\"status\":\"rejected\",\"code\":\"%s\","
                          "\"message\":\"%s\",\"ticket\":0}}",
                          result_id,UtcNow(),JsonEscape(code),JsonEscape(message));
     }
  };
