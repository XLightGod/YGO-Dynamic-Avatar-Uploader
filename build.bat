pip install -r requirements.txt
pyinstaller --onefile --windowed --icon=icon.ico --add-data "icon.ico;." --add-data "certs;certs" gui.py