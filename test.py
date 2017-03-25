import threading
import signal
import time
import redis
import math
import functools
import datetime

from itertools import islice

class MonitorThread(threading.Thread):
    def __init__(self, redis, capabilities, terminate_flag, measurement_period):
        super().__init__()

        self.redis = redis
        self.sensor_ids = list(capabilities.keys())
        self.capabilities = capabilities
        self.terminate_flag = terminate_flag
        self.measurement_period = measurement_period

    def run(self):
        while (not self.terminate_flag.is_set()):
            for sensor in self.sensor_ids:
                pipe = self.redis.pipeline()
                for cap in self.capabilities[sensor]["r"]:
                    if cap is not None: 
                        pipe.zrange("sensor:{sid}:{cid}:timestamps".format(sid=sensor, cid=cap), -1, -1)

                for cap in self.capabilities[sensor]["w"]:
                    if cap is not None: 
                        pipe.zrange("sensor:{sid}:{cid}:timestamps".format(sid=sensor, cid=cap), -1, -1)

                last_measurements = pipe.execute()
                last_activity = int(functools.reduce(lambda x,y: x[0] if (x[0] > y[0]) else y[0], last_measurements))
                print("sid\t{sid}\tlast activity {past}\tago :: {act}".format(sid=sensor, past=(datetime.datetime.now() - datetime.datetime.fromtimestamp(last_activity)), act=time.ctime(last_activity)))

            self.terminate_flag.wait(self.measurement_period)

        print("killed {sids}".format(sids=self.sensor_ids));


class StartupDetection(threading.Thread):
    def __init__(self,redis, terminate_flag, timeout=1.0):
        super().__init__()

        self.__timeout = timeout
        self.__pubsub = redis.pubsub()
        self.__terminate_flag = terminate_flag
        self.__pubsub.psubscribe("__keyspace@*__:sensors")

    def run(self):
        while (not self.__terminate_flag.is_set()):
            msg = self.__pubsub.get_message(timeout=self.__timeout)
            if msg:
                # TODO: if sensor added or removed, modify monitor threds
                print("Sensor list has been modified by {} operation".format(msg))

        print("exiting startup detector")


def get_capabilities(db):
    pipe = db.pipeline()
    pipe.smembers("sensors")
    pipe.hgetall("sensors:functions")
    pipe.hgetall("functions")
    return pipe.execute()

def decode_capabilities(to_parse, sensor_list, array_of_cap):
    ret_dict = {}
    for it in to_parse:
        if it in sensor_list:
            s_ret_dict = {}
            reads, writes, reports = to_parse[it].split(";")
            rkey, rcaps = reads.split(":")
            r = list(map(lambda x: array_of_cap[x] if (x in array_of_cap) else None, rcaps.split(",")))
            s_ret_dict[rkey] = r
            wkey, wcaps = writes.split(":")
            w = list(map(lambda x: array_of_cap[x] if (x in array_of_cap) else None, wcaps.split(",")))
            s_ret_dict[wkey] = w
            pkey, prep = reports.split(":")
            s_ret_dict[pkey] = prep
            ret_dict[it] = s_ret_dict
        else:
             print("There is no such device as {}".format(k))
    return ret_dict

def dict_chunks(data, size):
    it = iter(data)
    for i in range(0, len(data), size):
        yield {k:data[k] for k in islice(it, size)}

class MonitorThreadsManager(object):
    def __init__(self, redis, max_number_of_threads=8, measurement_period=10):
        self.__max_number_of_threads = max_number_of_threads
        self.__number_of_threads = 0
        self.__measurement_period = measurement_period
        self.__redis = redis
        self.__threads = []
        self.__flag = threading.Event()

    def start_monitors(self):
        sids, caps, array_of_cap = get_capabilities(self.__redis)

        if len(sids) < self.__max_number_of_threads:
            self.__number_of_threads = len(sids)
        else:
            self.__number_of_threads = self.__max_number_of_threads

        sensors = decode_capabilities(caps, sids, array_of_cap)
        how_many_per_thread = math.ceil(len(sensors)/self.__number_of_threads)

        print("Number of threads: {} Sensors in db: {} Sensors per thread: {}".format(self.__number_of_threads, len(sensors), how_many_per_thread))

        for sensors_to_process in dict_chunks(sensors, how_many_per_thread):
            t = MonitorThread(self.__redis, sensors_to_process, self.__flag, self.__measurement_period)
            t.start()
            self.__threads.append(t)

    def join_monitors(self):
        for thread in self.__threads:
            thread.join()

    def shutdown_monitors(self):
        self.__flag.set()

    def recalculate_monitor(self):
        self.shutdown_monitors()
        self.start_monitors()

def main():
    flag_manager = threading.Event()

    r = redis.StrictRedis(host='192.168.1.158', port=6379, db=0, encoding="utf-8", decode_responses=True)

    manager = MonitorThreadsManager(r)

    def do_exit(sig, stack):
        manager.shutdown_monitors()
        flag_manager.set()
        raise SystemExit("Exiting - all threads are being killed")

    signal.signal(signal.SIGINT, do_exit)

    manager.start_monitors();

    st = StartupDetection(r, flag_manager)
    st.start()
    st.join()

if __name__ == "__main__":
    main()
