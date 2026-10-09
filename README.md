# 3D Organizer

A Flask and SQLite workspace for 3D-printing materials, printers, models, orders, spending, and yearly sales. The app listens on all network interfaces at port 5005.

## Raspberry Pi

Install Python 3 and the `python3-venv` package, then run:

```sh
chmod +x start.sh
./start.sh
```

Open `http://<pi-address>:5005` from a device on the same network. Allow TCP port 5010 through the Pi firewall if one is enabled. To keep it available after reboot, run it as a systemd service. Set a private `SECRET_KEY` environment variable before exposing the app beyond a trusted local network.

There is currently no login screen. Keep the service on a trusted private network and do not forward its port to the public internet; orders include customer addresses and financial records.

The database and uploaded model images are stored in `data/`. Back up that folder regularly.

## Windows

Run `start.bat`; it creates a virtual environment, installs requirements, and starts the LAN-accessible server.
