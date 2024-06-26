import logging

from sys import exit
from selectors import DefaultSelector, EVENT_READ, EVENT_WRITE
from types import SimpleNamespace
from threading import Thread
from socket import socket, SOCK_STREAM, AF_INET

magicKey = b"India"

class EventEmitter:
    __events = dict()
    __listen=None
    stop=False
    
    def __init__(self):
        pass

    def attach(self, listener):
        "Attach a global listener which will be called at every event"
        self.__listen = listener
    
    def on(self, name, callback):
        # if self.stop : return
        if not bool(self.__events.get(name)):
            self.__events[name] = list()
        self.__events[name].append(callback)
        
        return len(self.__events[name])-1

    def emit(self, name, *args):
        if self.__listen:
            self.__listen(name, args)
        if name not in self.__events:
            return

        for callback in self.__events[name]:
            callback(args)
        pass

    def off(self,name, index:int=None):
        if not self.__events.get(name):
            return 
        if index is None:
            self.__events[name].clear()
        elif index < len(self.__events[name]):self.__events[name].pop(index)
    
    def off_all(self):
        for name in self.__events:
            self.off(name)

class Client():
    inb:list=None
    outb:list=None

class ServerSocket(EventEmitter):

    sel = DefaultSelector()
    clients = dict() # { portNumber<id[int]> : key<keySelector>}
    killThread = True
    eventThread = None
    ssock=None
    
    def __init__(self, addr:tuple) -> None:
        "pass the ip address and port number as argument in a tuple"
        super().__init__()
        self.sel = DefaultSelector()
        self.addr = addr
        pass

    def start(self):
        self.killThread = False
        if self.eventThread:
            return
        self.ssock = socket(AF_INET, SOCK_STREAM)
        self.ssock.bind(self.addr)
        self.ssock.listen()
        print("server listening at", self.addr)
        self.sel.register(self.ssock, EVENT_READ, data=None)

        self.eventThread = Thread(target=self._server_event_loop, daemon=True)
        self.eventThread.start()
        pass

    def sendTo(self, message:bytes|str,clientID=None):
        if type(message) is str:
            message = bytes(message, encoding="utf-8")

        if clientID not in self.clients:
            raise Exception(f"Client ID '{clientID}' not found")

        client_key = self.clients[clientID]
        data = client_key.data
        data.outb += message

    def sendAllTo(self, message:bytes, clientID):
        if type(message) is str:
            message = bytes(message, encoding="utf-8")

        if clientID not in self.clients:
            raise Exception(f"Client ID '{clientID}' not found")

        client_key = self.clients[clientID]
        csoc = client_key.fileobj
        # data.outb += message
        csoc.sendall(message)

    def broadcast(self, message:bytes):
        for clientID in self.clients:
            self.sendAllTo(message, clientID)

    def stop(self):
        while self.eventThread:
            self.killThread = True

    def handshake(self, data): # recieve handshake -> send handshake
        stage = data.handshakeStage
        if stage == 1:
            if data.inb == magicKey:
                data.inb = b""
            else:
                self.emit("handshake-failed", (stage, data.addr[1]))
                print("error : magicKey does not matches, recv:",data.inb)
        elif stage == 2:
            data.outb = magicKey
        pass

    def __add_connection(self, key):
        soc = key.fileobj
        conn, addr = soc.accept()
        clientID = addr[1]
        data = SimpleNamespace(addr=addr, outb=b"", inb=b"", clientID=clientID, handshakeStage=0)
        # HANDSHAKE STAGES # 1 -> Recieved | 2 -> Sent | 3 -> DONE
        self.sel.register(conn, EVENT_READ | EVENT_WRITE, data=data)
        self.clients[clientID] = SimpleNamespace(fileobj=conn, data=data)
        self.emit("new-connection", clientID)

    def __handle_RW_events(self, key, mask):
        soc = key.fileobj
        data = key.data
        if mask & EVENT_READ:
            recv = soc.recv(1024)
            if recv:
                data.inb += recv
                
                if data.handshakeStage == 0 :
                    data.handshakeStage = 1
                    self.handshake(data)
                    return
                self.emit("data-packet", {"clientID": data.clientID, "data": recv})
                # print(f"recv {data.addr}: "+recv.decode("utf-8"))
                # print(f"clients : {len(self.clients)}")
        if mask & EVENT_WRITE:
            if data.handshakeStage == 1:
                data.handshakeStage = 2
                self.handshake(data)
            
            # emit the `data` event and flushes the `inb` (input buffer)
            
            if data.inb:
                self.emit("data", {"clientID": data.clientID, "data": data.inb})
                data.inb = b""

            sent = 0
            if data.outb:
                sent = soc.send(data.outb)
            data.outb = data.outb[sent:]

            # verify handshake = no error after sending data (like client doesn't disconnected)
            if data.handshakeStage == 2:
                data.handshakeStage = 3 # handshake done
                print("HANDSHAKE DONE With", data.addr)
                self.emit("handshake-done")
        
    def _server_event_loop(self):
        print("server event_loop started")
        while not self.killThread:
            lastConnKey = None
            
            try:
                events = self.sel.select(timeout=None)
                for key,mask in events:
                    lastConnKey = key
                    if key.data is None:
                        # add new client
                        self.__add_connection(key)
                        pass
                    else :
                        # read / write clients
                        self.__handle_RW_events(key=key, mask=mask)

            except KeyboardInterrupt:
                print("Exiting by Keyboard Interrupt")

                exit(1)
            except Exception as e:
                print("Exiting (EventLoop) : ", e)
                logging.exception(f"An exception occurred: {e}")
                self._disconnect(lastConnKey)
            finally:
                pass

        self.eventThread = None
        pass

    def _disconnect(self, key):
        sock = None
        clientID = None
        
        if key.data is None:
            print("No `data` attr found in `key`")
            return
        try:
            sock = key.fileobj
            clientID = key.data.addr[1]

            self.sel.unregister(sock)
            sock.close()

            if clientID in self.clients:
                self.clients.pop(clientID)
        except Exception as e:
            print("ERORR DURING _disconnect\n",e, repr(key))
        else:
            self.emit("disconnected", clientID)

class ClientSocket(EventEmitter):
    sel = None
    addr = None
    data = None
    eventThread = None
    csoc = None
    handshakeStage = 0 #  1 -> send | 2 -> recived | 3 -> DONE
    stopThread=False

    def __init__(self, addr) -> None:
        super().__init__()
        self.sel = DefaultSelector()
        self.addr = addr

    def handshake(self, recv=None): # to verify the connection with server
        if self.handshakeStage ==3:
            print("Handshake already DONE")
            return
        # socket is ready to write
        
        try:
            if self.handshakeStage == 1:
                self.csoc.send(magicKey)
                return
            
            if self.handshakeStage == 2:
                if recv == magicKey:
                    self.handshakeStage = 3
                    print("Handshake Done with", self.addr)
                    self.emit("handshake-done")
                else:
                    e=Exception(f"MAGIC KEY DOES NOT MATCHES, {recv} != {magicKey}")
                    logging.exception(f"An exception occurred: {e}")
                    self.emit("handshake-error", e)

        except Exception as e:
            print("HANDSHAKE FAILED")
            logging.exception(f"An exception occurred: {e}")
            self.emit("handshake-error", e)
        else:
            # self.handshakeDone = True
            pass
        finally:
            pass

    def connect(self):
        csoc = socket(AF_INET, SOCK_STREAM)
        csoc.setblocking(False)
        csoc.connect_ex(self.addr)
        events = EVENT_WRITE | EVENT_READ

        self.data = SimpleNamespace(inb = b"", outb=b"")
        self.sel.register(csoc, events, data=self.data)
        self.csoc = csoc

        self.eventThread = Thread(target=self._client_event_loop, daemon=True)
        self.eventThread.start()

    def disconnect(self):
        try:
            self.sel.unregister(self.csoc)
        except:
            pass
        try:
            self.csoc.close()
        except:
            pass
        self.csoc = None
        self.stopThread=True
        # self.eventThread.join()

    def send(self, message:bytes):
        self.data.outb += message

    def __handle_RW_events(self, key, mask):
        sock = key.fileobj
        data = key.data
        if mask & EVENT_READ: # ready to read
            recv_data = sock.recv(1024)
            if recv_data:

                if self.handshakeStage == 1: # reveice magickey from server
                    self.handshakeStage = 2
                    self.handshake(recv_data)
                    return
                
                data.inb += recv_data
                self.emit("data-packet", recv_data)
        if mask & EVENT_WRITE: # ready to write
            if self.handshakeStage == 0: # sends handleshake magickey to server
                self.handshakeStage = 1
                self.handshake()
            if data.inb:
                self.emit("data", data.inb)
                data.inb = b"" # flush input buffer
            if data.outb:
                sent = sock.send(data.outb) 
                data.outb = data.outb[sent:]
        pass

    def _client_event_loop(self):
        print("Client EVENT LOOP STARTED")
        try:
            while True:
                if self.stopThread:
                    self.stopThread=False
                    return
                events = self.sel.select(timeout=None)
                for key, mask in events:
                    self.__handle_RW_events(key, mask)
        except KeyboardInterrupt:
            print("exiting (client) by keyboard interrupt")
            exit(2)
        except Exception as e:
            print("Exiting (client): " , e)
            logging.exception(f"An exception occurred: {e}")
            self.emit("error", e)
            self.sel.close()
            self.emit("disconnected")
        finally:
            pass
        print("Event Loop ended")

if __name__ == "__main__":
    ee = EventEmitter()
    ee.on("call", lambda arr : print(arr))
    ee.emit("call", 1,2,3,4)