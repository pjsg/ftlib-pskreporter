import os
import logging
import threading
import time
import random
import socket

SERVER_NAME = ("report.pskreporter.info", 4739)

LOG_ALL_FT8_INTERVAL = 900
LOG_ALL_OFFSET = 30

logging.basicConfig(
    format="%(levelname)s:%(name)s:%(asctime)s %(message)s",
    level=logging.WARNING,
    datefmt="%Y-%m-%d %H:%M:%S",
)


class PskReporter(object):
    sharedInstance = {}
    creationLock = threading.Lock()
    interval = 180

    @staticmethod
    def getSharedInstance(station: str):
        with PskReporter.creationLock:
            if PskReporter.sharedInstance.get(station) is None:
                PskReporter.sharedInstance[station] = PskReporter(station)
        return PskReporter.sharedInstance[station]

    @staticmethod
    def stop():
        [psk.cancelTimer() for psk in PskReporter.sharedInstance.values()]

    def __init__(
        self,
        callsign: str,
        grid: str,
        antenna: str,
        dummy: bool = False,
        tcp: bool = False,
    ):
        self.spots = []
        self.oldSpots = {}  # Indexed by timestamp
        self.spotLock = threading.Lock()
        self.station = {"callsign": callsign, "grid": grid, "antenna": antenna}
        self.uploader = Uploader(self.station, tcp=tcp)
        self.timer = None
        self.dummy = dummy
        self.total_spots = 0
        

    def getOldSpots(self):
        cutoff = time.time() - 1200
        for t in list(self.oldSpots.keys()):
            if t < cutoff:
                del self.oldSpots[t]
            else:
                for spot in self.oldSpots[t]:
                    yield spot

    def scheduleNextUpload(self):
        if self.timer:
            return
        delay = PskReporter.interval + random.uniform(0, 15)
        logging.info("scheduling next pskreporter upload in %3.2f seconds", delay)
        self.timer = threading.Timer(delay, self.upload)
        self.timer.name = "psk.uploader-%s" % self.station
        self.timer.start()

    def spotEquals(self, s1, s2):
        # s1 is the new spot
        keys = ["callsign", "timestamp", "locator", "db", "freq", "mode"]

        is_equal = (
            s1["callsign"] == s2["callsign"]
            and abs(s1["timestamp"] - s2["timestamp"]) < 1200
            and (s1["locator"] == s2["locator"] or not s1["locator"])
            and abs(s1["freq"] - s2["freq"]) < 10000
            and s1["mode"] == s2["mode"]
            and not s2.get("cheat", False)
        )

        if not is_equal:
           return False

        # Cheat mode to send all reports so we can get location data
        if s1["mode"] == "FT8":
            offset = (s1["timestamp"] % LOG_ALL_FT8_INTERVAL) - LOG_ALL_OFFSET
            if offset >= 0 and offset < 20:
                s1["cheat"] = True
                return False

        return True

    def addSpot(self, spot):
        self.spots.append(spot)
        self.addOldSpot(spot)

    def addOldSpot(self, spot):
        ts = spot["timestamp"]
        if ts not in self.oldSpots:
            self.oldSpots[ts] = []
        self.oldSpots[ts].append(spot)

    def spot(
        self,
        callsign,
        frequency,
        mode,
        timestamp=0,
        db=None,
        locator=None,
        hexbytes=None,
        dt=None,
    ):
        if not timestamp:
            timestamp = time.time()

        spot = {
            "callsign": callsign,
            "mode": mode,
            "locator": locator or "",
            "freq": frequency,
            "db": -128 if db is None else db,
            "timestamp": timestamp,
            "bytes": bytes.fromhex(hexbytes or ""),
            "dt": -32768 if dt is None else dt,
        }
        with self.spotLock:
            is_dupe = any(x for x in self.getOldSpots() if self.spotEquals(spot, x))

            if self.dummy:
                print("dupe" if is_dupe else "new", spot)
                if not is_dupe:
                    self.addOldSpot(spot)
                return

            if not is_dupe:
                self.addSpot(spot)

                self.scheduleNextUpload()

    def upload(self):
        try:
            with self.spotLock:
                self.timer = None
                spots = self.spots
                self.spots = []
            if spots:
                # Filter out very old spots
                cutoff = time.time() - 3000
                spot_count = len(spots)
                spots = [x for x in spots if x["timestamp"] >= cutoff]
                if len(spots) < spot_count:
                    logging.warning(
                        f"Dropping {spot_count - len(spots)} spots as too old (without connectivity). {len(spots)} left."
                    )
                if spots:
                    self.total_spots += len(spots)
                    unsent = self.uploader.upload(spots)
                    if unsent:
                        self.total_spots -= len(unsent)
                        # We want to save these for later
                        with self.spotLock:
                            self.spots = unsent + self.spots
        except Exception:
            logging.exception("Failed to upload spots")

    def cancelTimer(self):
        if self.timer:
            self.timer.cancel()
            self.timer.join()
        self.timer = None

    def close(self):
        self.cancelTimer()
        self.upload()
        if self.spots:
            logging.warning(f"Failed to upload {len(self.spots)} spots on close")
        logging.warning(f"Uploaded {self.total_spots} spots total")


class Uploader(object):
    receiverDelimiter = [0x99, 0x92]
    senderDelimiter = [0x99, 0x96]

    def __init__(self, station, tcp: bool = False):
        self.station = station
        # logging.debug("Station: %s", self.station)
        self.sequence = 0
        if tcp:
            self.upload = self.tcp_upload
            self.socket = None
        else:
            self.upload = self.udp_upload
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.id = os.urandom(4)

    def udp_upload(self, spots: list) -> list | None:
        logging.info("uploading %i spots using UDP", len(spots))
        for packet, chunk in self.getPackets(spots):
            self.sequence += len(chunk)
            self.socket.sendto(packet, SERVER_NAME)

        return None

    def tcp_upload(self, spots: list) -> list | None:
        logging.info("uploading %i spots using TCP", len(spots))
        failed_to_send = []
        sent_attempt = 0
        for packet, chunk in self.getPackets(spots, max_packet_length=25000):
            while sent_attempt < 5:
                sent_attempt += 1
                try:
                    if not self.socket:
                        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        self.socket.connect(SERVER_NAME)
                        self.socket.setsockopt(
                            socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1
                        )
                        self.socket_connected = True

                    self.socket.send(packet)
                    self.sequence += len(chunk)
                    sent_attempt = 0
                    break
                except Exception:
                    self.socket.close()
                    self.socket = None
            else:
                failed_to_send.extend(chunk)

        if failed_to_send:
            logging.warning(
                f"Failed to send {len(failed_to_send)} spots. Will retry later."
            )
        return failed_to_send

    def getPackets(self, spots, max_packet_length: int = 1400):
        encoded = []
        to_spot = {}
        for spot in spots:
            enc = self.encodeSpot(spot)
            if enc:
                encoded.append(enc)
                to_spot[enc] = spot

        def chunks(l, n):
            """Yield successive chunks from with a total length < n"""
            i = 0
            while i < len(l):
                length = n

                def inner(l, j, remaining):
                    while j < len(l) and remaining >= len(l[j]):
                        remaining -= len(l[j])
                        yield l[j]
                        j += 1

                chunk = list(inner(l, i, n))
                i += len(chunk)
                yield chunk

        rHeader = self.getReceiverInformationHeader()
        rInfo = self.getReceiverInformation()
        sHeader = self.getSenderInformationHeader()

        packets = []
        header_length = 16 + len(rHeader) + len(sHeader) + len(rInfo)
        for chunk in chunks(encoded, max_packet_length - header_length):
            sInfo = self.getSenderInformation(chunk)
            length = header_length + len(sInfo)
            header = self.getHeader(length)
            yield (
                header + rHeader + sHeader + rInfo + sInfo,
                [to_spot[item] for item in chunk],
            )

    def getHeader(self, length):
        return bytes(
            # protocol version
            [0x00, 0x0A]
            + list(length.to_bytes(2, "big"))
            + list(int(time.time()).to_bytes(4, "big"))
            + list(self.sequence.to_bytes(4, "big"))
            + list(self.id)
        )

    def encodeString(self, s):
        return [len(s)] + list(s.encode("utf-8"))

    def encodeBytes(self, b):
        return [len(b)] + list(b)

    def encodeSpot(self, spot):
        try:
            return bytes(
                self.encodeString(spot["callsign"])
                # freq in Hz to pskreporter
                + list(int(spot["freq"]).to_bytes(4, "big"))
                + list(int(spot["db"]).to_bytes(1, "big", signed=True))
                + self.encodeString(spot["mode"])
                + self.encodeString(spot["locator"])
                # informationsource. 1 means "automatically extracted
                + [0x01]
                + list(int(spot["timestamp"]).to_bytes(4, "big"))
                + self.encodeBytes(spot["bytes"])
                + list(int(spot["dt"]).to_bytes(2, "big", signed=True))
            )
        except Exception:
            logging.exception("Error while encoding spot for pskreporter")
            return None

    def getReceiverInformationHeader(self):
        return bytes(
            # id, length
            [0x00, 0x03, 0x00, 0x2C]
            + Uploader.receiverDelimiter
            # number of fields
            + [0x00, 0x04, 0x00, 0x01]
            # receiverCallsign
            + [0x80, 0x02, 0xFF, 0xFF, 0x00, 0x00, 0x76, 0x8F]
            # receiverLocator
            + [0x80, 0x04, 0xFF, 0xFF, 0x00, 0x00, 0x76, 0x8F]
            # decodingSoftware
            + [0x80, 0x08, 0xFF, 0xFF, 0x00, 0x00, 0x76, 0x8F]
            # antennaInformation
            + [0x80, 0x09, 0xFF, 0xFF, 0x00, 0x00, 0x76, 0x8F]
            # padding
            + [0x00, 0x00]
        )

    def getReceiverInformation(self):
        callsign = self.station["callsign"]
        locator = self.station["grid"]
        antennaInformation = (
            self.station["antenna"] if "antenna" in self.station else ""
        )
        decodingSoftware = "N1DQ-KA9Q-Radio/1.4"

        body = [
            b
            for s in [callsign, locator, decodingSoftware, antennaInformation]
            for b in self.encodeString(s or "")
        ]
        body = self.pad(body, 4)
        body = bytes(
            Uploader.receiverDelimiter + list((len(body) + 4).to_bytes(2, "big")) + body
        )
        return body

    def getSenderInformationHeader(self):
        return bytes(
            # id, length
            [0x00, 0x02, 0x00, 0x4C]
            + Uploader.senderDelimiter
            # number of fields
            + [0x00, 0x09]
            # senderCallsign
            + [0x80, 0x01, 0xFF, 0xFF, 0x00, 0x00, 0x76, 0x8F]
            # frequency
            + [0x80, 0x05, 0x00, 0x04, 0x00, 0x00, 0x76, 0x8F]
            # sNR
            + [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x76, 0x8F]
            # mode
            + [0x80, 0x0A, 0xFF, 0xFF, 0x00, 0x00, 0x76, 0x8F]
            # senderLocator
            + [0x80, 0x03, 0xFF, 0xFF, 0x00, 0x00, 0x76, 0x8F]
            # informationSource
            + [0x80, 0x0B, 0x00, 0x01, 0x00, 0x00, 0x76, 0x8F]
            # flowStartSeconds
            + [0x00, 0x96, 0x00, 0x04]
            # messageBits
            + [0x80, 0x0E, 0xFF, 0xFF, 0x00, 0x00, 0x76, 0x8F]
            # deltaT
            + [0x80, 0x0F, 0x00, 0x02, 0x00, 0x00, 0x76, 0x8F]
        )

    def getSenderInformation(self, chunk):
        sInfo = self.padBytes(b"".join(chunk), 4)
        sInfoLength = len(sInfo) + 4
        return bytes(Uploader.senderDelimiter) + sInfoLength.to_bytes(2, "big") + sInfo

    def pad(self, b, l):
        return b + [0x00 for _ in range(0, -1 * len(b) % l)]

    def padBytes(self, b, l):
        return b + bytes([0x00 for _ in range(0, -1 * len(b) % l)])
