This is a systemd service that reads the output from the KA9Q ft_lib when it is decoding FT4 and FT8. It converts this output
into data suitable for PSKReporter and submits it. The current version uses TCP in order to improve reliability.

To install, various files need to be edited and then copied:

* Add your callsign, locator and antenna to the ft\*-pskreporter.conf files

* The file `/etc/radio/*-pskreporter.conf` needs to be modified to enter OP's callsign, locator and "antenna information" (between " "). 

```
sudo cp ft*conf /etc/radio
sudo cp wspr*conf /etc/radio
sudo cp pskreporter@.service /etc/systemd/system/
```
Once that is done, the services can be started:

```
sudo systemctl start pskreporter@ft4 
sudo systemctl start pskreporter@ft8 
sudo systemctl start pskreporter@wspr 
```

If this works, then you probably want to do

```
sudo systemctl enable pskreporter@ft4 
sudo systemctl enable pskreporter@ft8 
sudo systemctl enable pskreporter@wspr 
```
