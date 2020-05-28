import socket
import random
import pickle
import time
import threading
import configparser
from queue import Queue
import select
import logging


# create socket and bind address to socket
server_address = ('localhost', 7777)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(server_address)

# queue for clients that wants connection
packet_queue_non_ack = Queue(maxsize=0)  # 0 = infinite size

# resource rep 1
lock = threading.RLock()
pack_non_que = threading.Condition(lock)

# queue for connected clients
packet_queue_ack = Queue(maxsize=0)

# resource rep 1
lock2 = threading.RLock()
pack_que = threading.Condition(lock2)

# que for internal sockets
socket_que = Queue(maxsize=0)

# package max pr second config init
config = configparser.ConfigParser()
config.read("opt.config")
MaxPackagePS = config['server']['MaxPackagePS']
MaxPackagePS = int(MaxPackagePS)

# ------ CORE PROCESSES ------------

def extract_odd_seq(msg):
    i = 4
    num = ""
    try:
        check = msg[0] + msg[1] + msg[2] + msg[3]

        while True:
            if msg[i] != "=" and check == "msg-" and str(msg[i]).isdigit():
                num = num + msg[i]
                i += 1
            else:
                break
    finally:
        if num == "":
            num = -1
        return int(num)


# --------------- LOGGING CONFIG ----------------------
def log_config_init():
    log = "logfile.log"
    logging.basicConfig(filename=log, level=logging.DEBUG, format='%(asctime)s %(message)s',
                        datefmt='%d/%m/%Y %H:%M:%S')


# ----------------- RESPONSE HANDLER --------------------
class ResponseHandler(threading.Thread):  # Distributor
    def __init__(self, address, packet):
        threading.Thread.__init__(self)
        self.address = address
        self.packet = packet

    def run(self):
        handle_response(self.address, self.packet)


def handle_response(this_address, packet):
    sock.sendto(packet, this_address)
    print("sent package")


# --------- REQUEST HANDLERS -----------------------------

class RequestHandler(threading.Thread):  # Request Handler
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        handle_requests()


def handle_requests():
    while True:
        try:
            data, address = sock.recvfrom(4096)
            data = pickle.loads(data)
            print('received "%s"' % repr(data))
            try:
                pack_non_que.acquire()
                packet_queue_non_ack.put([address, data])
                pack_non_que.notify()
            finally:
                pack_non_que.release()
        except socket.error:
            print("client terminated connection prematurely")


class RequestHandlerConnected(threading.Thread):  # Request Handler
    def __init__(self, this_addr, client_addr, packet):
        threading.Thread.__init__(self)
        self.this_addr = this_addr
        self.client_addr = client_addr
        self.packet = packet

    def run(self):
        handle_requests_connected(self.this_addr, self.client_addr, self.packet)


def handle_requests_connected(this_addr, client_address, packet):
    #Internal socket
    sock1 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = ('localhost', random.randint(49152, 65535))
    sock1.bind(addr)
    socket_que.put([client_address, addr])

    #External socket
    sock2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock2.bind(this_addr)
    sock2.settimeout(4)

    # max packages pr second
    counter = 0
    start = time.perf_counter()
    while True:
        elapsed = (time.perf_counter() - start)
        if elapsed >= 1:
            counter = 0
            start = time.perf_counter()
        else:
            if counter < MaxPackagePS:
                counter += 1
                try:
                    data, address = sock2.recvfrom(4096)
                    client_address = address
                    data = pickle.loads(data)
                    print('received"%s"' % repr(data))
                    try:
                        pack_que.acquire()
                        packet_queue_ack.put([client_address, data, None])
                        pack_que.notify()
                    finally:
                        pack_que.release()
                    time.sleep(0.1)
                    inp = [sock1]
                    inputready, o, e = select.select(inp, [], [], 0.0)
                    for s in inputready:
                        raise socket.timeout
                except socket.timeout:
                    try:
                        pack_que.acquire()
                        packet_queue_ack.put([client_address, packet, "con"])
                        pack_que.notify()
                    finally:
                        pack_que.release()
                    sock2.settimeout(0.5)
                    try:
                        data, address = sock2.recvfrom(4096)
                        data = pickle.loads(data)
                        print('received"%s"' % repr(data))
                        print("client received CON-RES, closing connecting")
                    except socket.timeout:
                        print("did not receive client ack, but time is up")
                    break

            else:
                try:
                    pack_que.acquire()
                    packet_queue_ack.put([client_address, packet, "max"])
                    pack_que.notify()
                finally:
                    pack_que.release()
                if not 1 - elapsed < 0:
                    sleeptime = 1 - elapsed
                    time.sleep(sleeptime)
                    """could do a select non blocking here to flush the socket
                     if you wanted all extra packages to be dropped"""


# --------- DISTRIBUTOR FOR NON ACK CLIENTS-----------------------------

class Distributor(threading.Thread):  # Distributor
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        distribute()


def distribute():
    process_list = []  # address, process_code, time
    while True:
        new = True
        try:
            pack_non_que.acquire()
            pack_non_que.wait()
            packet = packet_queue_non_ack.get()  # pop packet from que
        finally:
            pack_non_que.release()
        this_address = packet[0]  # address
        # remove idle processes from list
        end = time.time()
        process_list = [item for item in process_list if end - item[2] <= 3]

        for i in process_list:
            new = False
            process_code = three_way_handshake(i[1], packet, this_address)
            if process_code[0] == "kill":
                process_list.remove(i)
                response_handler_thread = ResponseHandler(this_address, process_code[1])
                response_handler_thread.start()
                print("alocated and sent new port and threads for client")
            elif process_code[0] == "bad":
                process_list.remove(i)
            else:  # this is if it is an ongoing process succes.
                process_list.remove(i)
                response_handler_thread = ResponseHandler(this_address, process_code[1])
                response_handler_thread.start()
        if new:
            process_code = three_way_handshake("hand-0", packet, this_address)
            if process_code[0] != "bad":
                process_list.append([this_address, process_code[0], time.time()])
                response_handler_thread = ResponseHandler(this_address, process_code[1])
                response_handler_thread.start()
            else:
                print("wrong INIT request")

# --------- DISTRIBUTOR FOR ACKNOWLEDGED CLIENTS-----------------------------


class DistributorAck(threading.Thread):  # Distributor
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        distribute_ack()


def distribute_ack():
    process_list = [] # address, process_code, seq_nmr
    socket_list = []
    while True:
        new = True
        try:
            pack_que.acquire()
            pack_que.wait()
            while socket_que.empty() is False:
                socket_list.append(socket_que.get())
            while packet_queue_ack.empty() is False:
                packet = packet_queue_ack.get()  # pop packet from que
                this_address = packet[0]  # address
                for i in process_list:
                    if i[0] == this_address:
                        new = False
                        process_code = protocols_ack(i[1], packet, i[2], packet[2])
                        if process_code[0] == "CON":
                            response_handler_thread = ResponseHandler(this_address, process_code[1])
                            response_handler_thread.start()
                            process_list.remove(i)
                            socket_list = [item for item in socket_list if this_address != item[0]]
                        elif process_code[0] == "MAX":
                            response_handler_thread = ResponseHandler(this_address, process_code[1])
                            response_handler_thread.start()
                        elif process_code[0] == "CONH":
                            pass
                        elif process_code[0] == "kill":
                            process_list.remove(i)
                            response_handler_thread = ResponseHandler(this_address, process_code[1])
                            response_handler_thread.start()
                        elif process_code[0] != "bad":  # this is if it is an ongoing process success.
                            i[2] += 2
                            i[1] = process_code[0]  # assign this process the new process code
                            response_handler_thread = ResponseHandler(this_address, process_code[1])
                            response_handler_thread.start()
                        else:
                            for i in socket_list:
                                if i[0] == this_address:
                                    try:
                                        print("scuttled ship")
                                        data_string = pickle.dumps("scuttle")
                                        sock.sendto(data_string, i[1])
                                        process_list = [item for item in process_list if this_address != item[0]]
                                        socket_list = [item for item in socket_list if this_address != item[0]]
                                    except Exception:
                                        print("could not terminate..")

                if new:
                    init_seq = 0
                    if packet[2] == "con":
                        process_code = protocols_ack("CON", packet, 0, packet[2])
                        response_handler_thread = ResponseHandler(this_address, process_code[1])
                        response_handler_thread.start()
                        socket_list = [item for item in socket_list if this_address != item[0]]
                    else:
                        process_code = protocols_ack("chat-true", packet, (init_seq - 1), packet[2])
                        if process_code[0] == "CONH":
                            pass
                        elif process_code[0] != "bad" and init_seq != -1:
                            process_list.append([this_address, process_code[0], init_seq + 1])
                            response_handler_thread = ResponseHandler(this_address, process_code[1])
                            response_handler_thread.start()
                        else:
                            for i in socket_list:
                                if i[0] == this_address:
                                    try:
                                        data_string = pickle.dumps("scuttle")
                                        sock.sendto(data_string, i[1])
                                        socket_list = [item for item in socket_list if this_address != item[0]]
                                        print("scuttle ship no process init")
                                    except Exception:
                                        print("could not terminate..")
        finally:
            pack_que.release()


# ------------------- PROTOCOLS --------------------------

def three_way_handshake(process_info, packet, client_addr):

    if process_info == "hand-0" and packet[1] == "com-0":
        data_string = pickle.dumps("com-0 accept")
        logging.info(str(packet[1]) + " <" + str(client_addr[1]) + ">")
        logging.info(str("com-0 accept") + " <" + str(server_address[1]) + ">")
        print("sending syn-ack to client")
        return ["hand-1", data_string]

    elif process_info == "hand-1" and packet[1] == "com-0 accept":
        server_address_new = ('localhost', random.randint(49152, 65535))
        data_string = pickle.dumps(server_address_new)
        handle_requests_connected_thread = RequestHandlerConnected(server_address_new, client_addr, packet[1])
        handle_requests_connected_thread.start()
        logging.info(str(packet[1]))
        logging.info("Success. Serv sock IP: " + "<" + str(server_address_new[1]) + ">" +
                     ", client sock IP: " + "<" + str(client_addr[1]) + ">")
        print("three way handshake complete")
        return ["kill", data_string]
    else:
        logging.info("Illegitimate packet from" + " <" + str(client_addr[1]) + ">")
        return ["bad", "none"]

# ---------------------- PROTOCOL FOR ACKNOWLEDGED CLIENTS -------------------


def protocols_ack(process_info, packet, our_seq, inf):

    if inf == "con":
        data_string = pickle.dumps("con-res 0xFE")
        return ["CON", data_string]
    elif inf == "max":
        data_string = pickle.dumps("Maximum packages pr second reached, all packages after this one: "
                                   + "will have to wait to be processed")
        return ["MAX", data_string]
    elif packet[1] == "con-h 0x00":
        return ["CONH", "none"]
    elif process_info == "chat-true" and (our_seq + 1) == extract_odd_seq(packet[1]):
        load = "res-" + str(our_seq + 2) + "= i am server"
        data_string = pickle.dumps(load)
        return ["chat-true", data_string]
    else:
        return ["bad", "none"]

# --------------- MAIN --------------------


log_config_init()

request_handler_thread = RequestHandler()
distributor_thread = Distributor()
distribute_ack_thread = DistributorAck()

request_handler_thread.start()
distributor_thread.start()
distribute_ack_thread.start()






