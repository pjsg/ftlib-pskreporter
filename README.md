# ftlib-pskreporter

This is a systemd service that reads the output produced by the KA9Q `ft_lib` decoders and converts it into data suitable for submission to PSK Reporter.

The current version supports the following modes:

- FT4
- FT8
- WSPR
- JS8

The current version uses TCP in order to improve reliability.

## Installation

Various files need to be edited and then copied.

Add your callsign, locator and antenna details to the appropriate `*-pskreporter.conf` files.

The files in `/etc/radio/` should be modified to contain the operator's callsign, locator and antenna description.

```bash
sudo cp ft*conf /etc/radio
sudo cp wspr*conf /etc/radio
sudo cp js8*conf /etc/radio
sudo cp pskreporter@.service /etc/systemd/system/
```

Once that is done, the services can be started:

```bash
sudo systemctl start pskreporter@ft4
sudo systemctl start pskreporter@ft8
sudo systemctl start pskreporter@wspr
sudo systemctl start pskreporter@js8
```

If everything is working correctly, the services can then be enabled:

```bash
sudo systemctl enable pskreporter@ft4
sudo systemctl enable pskreporter@ft8
sudo systemctl enable pskreporter@wspr
sudo systemctl enable pskreporter@js8
```

## Configuration

Example FT8 configuration:

```text
FILE=/var/log/ft8.log
MODE=ft8
CALLSIGN=N1DQ-999
LOCATOR=FN42hn
ANTENNA="200ft Longwire"
```

Example JS8 configuration:

```text
FILE=/var/log/js8.log
MODE=js8
CALLSIGN=N1DQ-999
LOCATOR=FN42hn
ANTENNA="200ft Longwire"
```

## JS8 input format

JS8 input records are expected to have the following format:

```text
2025/10/30 17:47:30 -20.0 0.3 A 7.080208 KD6XU/MM RG85 ~ KDVVV/MM:
```

The fields are interpreted as follows:

| Field | Description |
|------|------|
| Timestamp | UTC date and time |
| SNR | Signal-to-noise ratio (dB) |
| Offset | Time offset |
| Mode | JS8 submode |
| Frequency | Frequency in MHz |
| Callsign | Decoded callsign |
| Locator | Maidenhead grid square |
| Message | Message contents |

## Service operation

The `pskreporter@.service` unit uses the instance name to select the corresponding configuration file.

For example:

```bash
sudo systemctl start pskreporter@js8
```

loads:

```text
/etc/radio/js8-pskreporter.conf
```

Similarly:

```bash
sudo systemctl start pskreporter@ft8
```

loads:

```text
/etc/radio/ft8-pskreporter.conf
```