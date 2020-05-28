import socket
import random
import pickle
import time
import threading
import configparser
import select
from queue import Queue
import os

server_address = ('localhost', 7777)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# queue for requests
packet_queue = Queue(maxsize=0)  # 0 = infinite size

# Que for client keyboard input
input_que = Queue(maxsize=10)

# resource rep 1
lock = threading.RLock()
inp_que = threading.Condition(lock)

# resource rep 1
lock2 = threading.RLock()
pack_que = threading.Condition(lock2)

# Heartbeat
config = configparser.ConfigParser()
config.read("opt.config")
keepAlive = config['client']['KeepAlive']
if keepAlive == 'True':
    keepAlive = True
else:
    keepAlive = False



# ------ CORE PROCESSES ------------

def extract_odd_seq(msg):
    i = 4
    num = ""
    try:
        while True:
            if msg[i] != "=" and str(msg[i]).isdigit():
                num = num + msg[i]
                i += 1
            else:
                break
    finally:
        if num == "":
            num = -1
        return int(num)


# --- Classes that spawn threads
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


# --------- REQUEST HANDLERS -----------------------------


class RequestHandler(threading.Thread):  # Request Handler
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        handle_requests()


def handle_requests():
    while True:
        data, address = sock.recvfrom(4096)
        data = pickle.loads(data)
        print('received "%s"' % repr(data))
        try:
            pack_que.acquire()
            packet_queue.put([server_address, data])
            pack_que.notify()
        finally:
            pack_que.release()


# --------------- KEYBOARD LISTENER----------------------------


class KeyBoardListener(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        listen_for_input()


def listen_for_input():
    msg_seq = 0
    input_thing_thread = InputThing()
    input_thing_thread.start()
    while True:
        try:
            inp_que.acquire()
            inp_que.wait(3)
            if input_que.empty() and keepAlive == True:
                data_string = pickle.dumps("con-h 0x00")
                response_handler_thread = ResponseHandler(server_address, data_string)
                response_handler_thread.start()
            elif not input_que.empty():
                load = "msg-" + str(msg_seq) + "=" + str(input_que.get())
                msg_seq += 2
                data_string = pickle.dumps(load)
                response_handler_thread = ResponseHandler(server_address, data_string)
                response_handler_thread.start()
            else:
                pass
        finally:
            inp_que.release()



class InputThing(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        listen()


def listen():
    while True:
        inp = input()
        try:
            input_que.put(inp)
            inp_que.acquire()
            inp_que.notify()
        finally:
            inp_que.release()


# --------------INIT FUNCTION------------------------------


def init_connection():
    data_string = pickle.dumps("com-0")
    print("sending syn to server")
    return ["hand-0", data_string]


# --------- DISTRIBUTOR -----------------------------


class Distributor(threading.Thread):  # Distributor
    def __init__(self):
        threading.Thread.__init__(self)

    def run(self):
        distribute()


def distribute():
    global server_address

    process_list = []  # address, process_code, seq

    process_code = init_connection()  # init three way handshake
    response_handler_thread = ResponseHandler(server_address, process_code[1])
    response_handler_thread.start()

    time.sleep(0.1)  # needs to wait until sendto has happened
    request_handler_thread = RequestHandler()
    request_handler_thread.start()

    while True:
        new = True
        try:
            pack_que.acquire()
            pack_que.wait()
            packet = packet_queue.get()  # pop packet from que
        finally:
            pack_que.release()

        for i in process_list:
            if i[0] == server_address:  # if ongoing process
                new = False
                process_code = ongoing_process(i[1], packet, i[2])

                if process_code[0] == "FIN":
                    response_handler_thread = ResponseHandler(server_address, process_code[1])
                    response_handler_thread.start()
                    time.sleep(0.2)
                    print("Exiting")
                    os._exit(1)

                elif process_code[0] == "syn-ack-complete":
                    i[1] = "chat-true"
                    server_address = process_code[1]
                    i[0] = server_address
                    input_thread = KeyBoardListener()
                    input_thread.start()

                elif process_code[0] == "chat-true":
                    i[2] += 2
                    i[1] = process_code[0]

                elif process_code[0] != "bad":  # update process_code
                    i[1] = process_code[0]
                    response_handler_thread = ResponseHandler(server_address, process_code[1])
                    response_handler_thread.start()
                else:
                    pass

        if new:
            process_code = ongoing_process("hand-0", packet, 0)
            if process_code[0] != "bad":
                process_list.append([server_address, process_code[0], 0])
                response_handler_thread = ResponseHandler(server_address, process_code[1])
                response_handler_thread.start()
            else:
                print("Discarded packet")



# ------------------- PROTOCOLS --------------------------

def ongoing_process(process_info, packet, our_seq):

    payload = packet[1]

    if payload == "con-res 0xFE":
        data_string = pickle.dumps("con-res 0xFF")
        print("sending con-res 0xFF to server")
        return ["FIN", data_string]

    elif process_info == "hand-0" and payload == "com-0 accept":
        data_string = pickle.dumps("com-0 accept")
        print("sending (syn)ack to server")
        return ["recv_sock", data_string]

    elif process_info == "recv_sock":
        print("socket received")
        return ["syn-ack-complete", payload]  # payload is the new server address

    elif process_info == "chat-true" and extract_odd_seq(payload) == our_seq + 1:
        return ["chat-true", None]

    else:
        return ["bad", None]


# --------------- MAIN --------------------

distributor_thread = Distributor()

distributor_thread.start()

