'use strict';
// Connection and QoS are independent of the visual components.
// Each retry keeps the same client_id; only a persisted server ACK completes a send.
class IMTransport {
  constructor(onEvent, onState) {
    this.onEvent=onEvent; this.onState=onState; this.pending=new Map();
    this.attempt=0; this.stopped=false; this.socket=null; this.lastPong=Date.now();
    this.connect();
  }
  connect() {
    if(this.stopped)return;
    const socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/socket`);
    this.socket=socket;
    socket.onopen=()=>{
      if(this.socket!==socket)return;
      this.attempt=0;this.lastPong=Date.now();this.onState(true);
      for(const item of this.pending.values())this.transmit(item);
      clearInterval(this.heartbeat);
      this.heartbeat=setInterval(()=>{
        if(Date.now()-this.lastPong>35000){socket.close();return;}
        if(socket.readyState===WebSocket.OPEN)socket.send(JSON.stringify({op:'ping'}));
      },10000);
    };
    socket.onmessage=event=>{
      if(this.socket!==socket)return;
      let data;try{data=JSON.parse(event.data);}catch{return;}
      this.lastPong=Date.now();
      if(data.type==='pong')return;
      if(data.type==='ack' || data.type==='nack') {
        const item=this.pending.get(data.client_id);
        if(item){clearTimeout(item.timer);this.pending.delete(data.client_id);if(data.type==='ack')item.resolve(data.message);else item.reject(new Error(data.error||'这封小笺暂时没有寄出。'));}
        if(data.type==='ack')this.onEvent({type:'message',message:data.message});
        return;
      }
      this.onEvent(data);
    };
    socket.onerror=()=>{};
    socket.onclose=()=>{
      if(this.socket!==socket || this.stopped)return;
      clearInterval(this.heartbeat);this.onState(false);
      const delay=Math.min(15000,1000*2**Math.min(this.attempt++,4))+Math.random()*300;
      clearTimeout(this.reconnect);this.reconnect=setTimeout(()=>this.connect(),delay);
    };
  }
  send(payload) {
    if(this.stopped || this.socket?.readyState!==WebSocket.OPEN)return Promise.reject(new Error('连接暂时断开了，小笺已留在这里，重连后可以重试。'));
    return new Promise((resolve,reject)=>{
      const item={payload:{...payload,op:'message'},resolve,reject,attempts:0,timer:null};
      this.pending.set(payload.client_id,item);this.transmit(item);
    });
  }
  transmit(item) {
    clearTimeout(item.timer);
    if(item.attempts>=3){this.pending.delete(item.payload.client_id);item.reject(new Error('暂时没有收到确认，点击消息旁的「重试」重新寄出。'));return;}
    item.attempts++;
    if(this.socket?.readyState===WebSocket.OPEN)this.socket.send(JSON.stringify(item.payload));
    item.timer=setTimeout(()=>this.transmit(item),6000);
  }
  close() {
    this.stopped=true;clearInterval(this.heartbeat);clearTimeout(this.reconnect);this.socket?.close();
    for(const item of this.pending.values()){clearTimeout(item.timer);item.reject(new Error('连接已关闭，小笺还在本地。'));}
    this.pending.clear();
  }
}
