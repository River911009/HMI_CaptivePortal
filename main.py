import network,binascii
from Server import *

class CaptivePortal:
  def __init__(self,essid=None):
    sta=network.WLAN(network.STA_IF)
    sta.active(False)
    del sta
    self.ap_if=network.WLAN(network.AP_IF)
    self.essid=essid if essid!=None else "ESP8266-%s"%(binascii.hexlify(self.ap_if.config("mac")[-3:]).decode())
    self.dns_server=None
    self.http_server=None
    self.poller=select.poll()
  def start(self,ip,submask,gateway,dns):
    self.server_ip=ip
    self.ap_if.active(True)
    self.ap_if.ifconfig((ip,submask,gateway,dns))
    self.ap_if.config(essid=self.essid,authmode=network.AUTH_OPEN)

    print("Access point started: %s\n"%(self.essid),self.ap_if.ifconfig())
  def captive_portal(self):

    print("Starting captive portal...")
    self.start(ip="192.168.0.1",submask="255.255.255.0",gateway="192.168.0.1",dns="192.168.0.1")
    if self.http_server==None:
      self.http_server=HTTPServer(self.poller,self.server_ip)

      print("Configured HTTP server")
    if self.dns_server==None:
      self.dns_server=DNSServer(self.poller,self.server_ip)

      print("Configured DNS server")
    try:
      while True:
        gc.collect()
        for response in self.poller.ipoll(1000):
          sock,event,*others=response
          is_handled=self.handle_dns(sock,event,others)
          if not is_handled:
            self.handle_http(sock,event,others)
          #self.handle_dns(sock,event,others)
    except KeyboardInterrupt:
      print("Captive portal stopped")
    self.cleanup()
  def handle_http(self,sock,event,others):
    self.http_server.handle(sock,event,others)
  def handle_dns(self,sock,event,others):
    if sock==self.dns_server.sock:
      if event==select.POLLHUP:
        return(True)
      self.dns_server.handle(sock,event,others)
      return(True)
    return(False)
  def cleanup(self):
    print("Cleaning up...")
    if self.dns_server:
      self.dns_server.stop(self.poller)
    gc.collect()


if __name__=="__main__":
  interf=CaptivePortal("HMI_CaptivePortal")
  interf.captive_portal()
