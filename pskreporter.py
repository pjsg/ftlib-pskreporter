import os
import logging
import threading
import time
import random
import socket


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

    def __init__(self, callsign: str, grid: str, antenna: str):
        self.spots = []
        self.oldSpots = {}  # Indexed by timestamp
        self.spotLock = threading.Lock()
        self.station = {"callsign": callsign, "grid": grid, "antenna": antenna}
        self.uploader = Uploader(self.station)
        self.timer = None

    def getOldSpots(self):
        cutoff = time.time() - 900
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
        logging.info(
            "scheduling next pskreporter upload in %3.2f seconds", delay)
        self.timer = threading.Timer(delay, self.upload)
        self.timer.name = "psk.uploader-%s" % self.station
        self.timer.start()

    def spotEquals(self, s1, s2):
        # s1 is the new spot
        keys = ["callsign", "timestamp",
                "locator", "db", "freq", "mode"]

        return (s1['callsign'] == s2['callsign'] and
               abs(s1['timestamp'] - s2['timestamp']) < 900 and
               (s1['locator'] == s2['locator'] or not s1['locator']) and
               abs(s1['freq'] - s2['freq']) < 10000 and
               s1['mode'] == s2['mode'])

    def addSpot(self, spot):
        self.spots.append(spot)
        ts = spot['timestamp']
        if ts not in self.oldSpots:
            self.oldSpots[ts] = []
        self.oldSpots[ts].append(spot)

    def spot(self, callsign, frequency, mode, timestamp=0, db=None, locator=None):
        if not timestamp:
            timestamp = time.time()

        spot = {
            "callsign": callsign,
            "mode": mode,
            "locator": locator or '',
            "freq": frequency,
            "db": -128 if db is None else db,
            "timestamp": timestamp
        }
        with self.spotLock:
            if any(x for x in self.getOldSpots() if self.spotEquals(spot, x)):
                # dupe
                pass
            else:
                self.addSpot(spot)

            self.scheduleNextUpload()

    def upload(self):
        try:
            with self.spotLock:
                self.timer = None
                spots = self.spots
                self.spots = []
            if spots:
                self.uploader.upload(spots)
        except Exception:
            logging.exception("Failed to upload spots")

    def cancelTimer(self):
        if self.timer:
            self.timer.cancel()
            self.timer.join()
        self.timer = None


class Uploader(object):
    receiverDelimiter = [0x99, 0x92]
    senderDelimiter = [0x99, 0x93]

    def __init__(self, station):
        self.station = station
        # logging.debug("Station: %s", self.station)
        self.sequence = 0
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.id = os.urandom(4)

    def upload(self, spots):
        logging.warning("uploading %i spots               ", len(spots))
        for packet in self.getPackets(spots):
            self.socket.sendto(packet, ("report.pskreporter.info", 4739))

    def getPackets(self, spots):
        encoded = [self.encodeSpot(spot) for spot in spots]
        # filter out any erroneous encodes
        encoded = [e for e in encoded if e is not None]

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
        # 75 seems to be a safe bet
        for chunk in chunks(encoded, 1400 - header_length):
            sInfo = self.getSenderInformation(chunk)
            length = header_length + len(sInfo)
            header = self.getHeader(length)
            yield header + rHeader + sHeader + rInfo + sInfo

    def getHeader(self, length):
        self.sequence += 1
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
            + [0x00, 0x04, 0x00, 0x00]
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
        antennaInformation = self.station["antenna"] if "antenna" in self.station else ""
        decodingSoftware = "N1DQ-Importer-KA9Q-Radio"

        body = [b for s in [callsign, locator, decodingSoftware,
                            antennaInformation] for b in self.encodeString(s)]
        body = self.pad(body, 4)
        body = bytes(Uploader.receiverDelimiter +
                     list((len(body) + 4).to_bytes(2, "big")) + body)
        return body

    def getSenderInformationHeader(self):
        return bytes(
            # id, length
            [0x00, 0x02, 0x00, 0x3C]
            + Uploader.senderDelimiter
            # number of fields
            + [0x00, 0x07]
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
        )

    def getSenderInformation(self, chunk):
        sInfo = self.padBytes(b"".join(chunk), 4)
        sInfoLength = len(sInfo) + 4
        return bytes(Uploader.senderDelimiter) + sInfoLength.to_bytes(2, "big") + sInfo

    def pad(self, b, l):
        return b + [0x00 for _ in range(0, -1 * len(b) % l)]

    def padBytes(self, b, l):
        return b + bytes([0x00 for _ in range(0, -1 * len(b) % l)])

if __name__ == "__main__":
    rep = PskReporter('N1DQ', 'FN42hn', '200 ft longwire')

    rep.spot(
        callsign = "N1DQ-1",
        mode = "FT8",
        locator = "FN42hn",
        frequency = 14070000,
        db = 12,
        timestamp = time.time()
        )

    rep.upload()

