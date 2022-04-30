import uerrno,uio,gc
import socket,select
from machine import Pin,ADC
import random,dht
btn=Pin(0,Pin.IN)
sensor=dht.DHT11(Pin(2))
vb_ctrl=Pin(5,Pin.OUT)
vb_value=ADC(0)

from collections import namedtuple
WriteConn=namedtuple("WriteConn",["body","buff","buffmv","write_range"])
ReqInfo=namedtuple("ReqInfo",["type","path","params","host"])

class Server:
  def __init__(self,poller,port,sock_type,name):
    self.name=name
    self.sock=socket.socket(socket.AF_INET,sock_type)
    self.poller=poller
    self.poller.register(self.sock,select.POLLIN)
    addr=socket.getaddrinfo("0.0.0.0",port)[0][-1]
    self.sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    self.sock.bind(addr)

    print(self.name,"listening on",addr)
  def stop(self,poller):
    poller.unregister(self.sock)
    self.sock.close()

    print(self.name,"stopped")

class DNSQuery:
  def __init__(self,data):
    self.data=data
    self.domain=""
    head=12
    length=data[head]
    while length!=0:
      label=head+1
      self.domain+=data[label:label+length].decode("utf-8")+"."
      head+=length+1
      length=data[head]
  def answer(self,ip):
    packet=self.data[:2]
    packet+=b"\x81\x80"
    packet+=self.data[4:6]+self.data[4:6]
    packet+=b"\x00\x00\x00\x00"
    packet+=self.data[12:]
    packet+=b"\xC0\x0C"
    packet+=b"\x00\x01\x00\x01"
    packet+=b"\x00\x00\x00\x3C"
    packet+=b"\x00\x04"
    packet+=bytes(map(int,ip.split(".")))
    gc.collect()
    return(packet)

class DNSServer(Server):
  def __init__(self,poller,ip_addr):
    super().__init__(poller,53,socket.SOCK_DGRAM,"DNS Server")
    self.ip_addr=ip_addr
  def handle(self,sock,event,others):
    if sock!=self.sock:
      return
    try:
      data,sender=sock.recvfrom(1024)
      request=DNSQuery(data)
      sock.sendto(request.answer(self.ip_addr),sender)

      print("Sending %s -> %s"%(request.domain,self.ip_addr))
      del request
      gc.collect()
    except Exception as e:
      print("DNS server exception:",e)

class HTTPServer(Server):
  def __init__(self,poller,ip_addr):
    super().__init__(poller,80,socket.SOCK_STREAM,"HTTP Server")
    self.ip_addr=ip_addr if type(ip_addr)==bytes else ip_addr.encode()
    self.request=dict()
    self.conns=dict()
    self.routes={
      b"/":b"./index.html",
      b"/setData":self.config,
      b"/title":self.title,
      b"/getData":self.measure,
    }
    self.sock.listen(10)
    self.sock.setblocking(False)
  def config(self,params):
    ssid=params.get(b"ssid",None)
    password=params.get(b"password",None)
    if all([ssid,password]):

      print([ssid,password])
    headers=(
      b"HTTP/1.1 307 Temporary Redirect\r\n"
      b"Location: http://%s\r\n"%(self.ip_addr)
    )
    return(b"",headers)
  def title(self,params):
    headers=b"HTTP/1.1 200 OK\r\n"
    return(b"%d"%(random.getrandbits(16)),headers)
  def measure(self,params):
    headers=b"HTTP/1.1 200 OK\r\n"
    vb=0
    vb_ctrl.value(1)
    try:
      sensor.measure()
      vb=vb_value.read()
      vb_ctrl.value(0)
    except:
      pass
    return(
      b"Temp: %2d °C<br /> Humi: %2d %%<br />Vbat: %d"%(
        sensor.temperature(),
        sensor.humidity(),
        vb
      ),
      headers
    )

  def handle(self,sock,event,others):
    if sock==self.sock:
      print("Accepting new HTTP connection")
      self.accept(sock)
    elif event&select.POLLIN:
      print("Reading incoming HTTP data")
      self.read(sock)
    elif event&select.POLLOUT:
      print("Sending outgoing HTTP data")
      self.write_to(sock)
  def accept(self,s):
    try:
      client_sock,addr=s.accept()
    except OSError as e:
      if e.args[0]==uerrno.EAGAIN:
        return
    client_sock.setblocking(False)
    client_sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    self.poller.register(client_sock,select.POLLIN)
  def parse_request(self,req):
    req_lines=req.split(b"\r\n")
    req_type,full_path,http_ver=req_lines[0].split(b" ")
    path=full_path.split(b"?")
    base_path=path[0]
    query=path[1] if len(path)>1 else None
    query_params=(
      {
        key:val
        for key,val in [param.split(b"=") for param in query.split(b"&")]
      }
      if query
      else {}
    )
    host=[line.split(b": ")[1] for line in req_lines if b"Host:" in line][0]
    return(ReqInfo(req_type,base_path,query_params,host))
  def get_response(self,req):
    headers=b"HTTP/1.1 200 OK\r\n"
    route=self.routes.get(req.path,None)
    if type(route) is bytes:
      return(open(route,"rb"),headers)
    if callable(route):
      response=route(req.params)
      body=response[0] or b""
      headers=response[1] or headers
      return(uio.BytesIO(body),headers)
    headers=b"HTTP/1.1 404 Not Found\r\n"
    return(uio.BytesIO(b""),headers)
  def is_valid_req(self,req):
    if req.host!=self.ip_addr:
      return(False)
    return(req.path in self.routes)
  def read(self,s):
    data=s.read()
    if not data:
      self.close(s)
      return
    sid=id(s)
    self.request[sid]=self.request.get(sid,b"")+data
    if data[-4:]!=b"\r\n\r\n":
      return
    req=self.parse_request(self.request.pop(sid))
    if not self.is_valid_req(req):
      headers=(
        b"HTTP/1.1 307 Temporary Redirect\r\n"
        b"Location: http://%s/\r\n"%(self.ip_addr)
      )
      body=uio.BytesIO(b"")
      self.prepare_write(s,body,headers)
      return
    body,headers=self.get_response(req)
    self.prepare_write(s,body,headers)
  def prepare_write(self,s,body,headers):
    headers+="\r\n"
    buff=bytearray(headers+"\x00"*(536-len(headers)))
    buffmv=memoryview(buff)
    bw=body.readinto(buffmv[len(headers):],536-len(headers))
    c=WriteConn(body,buff,buffmv,[0,len(headers)+bw])
    self.conns[id(s)]=c
    self.poller.modify(s,select.POLLOUT)
  def write_to(self,sock):
    c=self.conns[id(sock)]
    if c:
      bytes_written=sock.write(c.buffmv[c.write_range[0]:c.write_range[1]])
      if not bytes_written or c.write_range[1]<536:
        self.close(sock)
      else:
        self.buff_advance(c,bytes_written)
  def buff_advance(self,c,bytes_written):
    if bytes_written==c.write_range[1]-c.write_range[0]:
      c.write_range[0]=0
      c.write_range[1]=c.body.readinto(c.buff,536)
    else:
      c.write_range[0]+=bytes_written
  def close(self,s):
    s.close()
    self.poller.unregister(s)
    sid=id(s)
    if sid in self.request:
      del self.request[sid]
    if sid in self.conns:
      del self.conns[sid]
    gc.collect()
