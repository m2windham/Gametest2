import sys
import json
import socket
import threading
import time
from datetime import datetime
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QListWidget, QListWidgetItem, QSplitter, QFrame
)
from PySide6.QtCore import Qt, Signal, QObject

HOST = 'localhost'
PORT = 9876

class MCPClientThread(threading.Thread):
    def __init__(self, log_callback, error_callback, stats_callback):
        super().__init__(daemon=True)
        self.log_callback = log_callback
        self.error_callback = error_callback
        self.stats_callback = stats_callback
        self.running = True
        self.sock = None
        self.success_count = 0
        self.error_count = 0
        self.latencies = []

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((HOST, PORT))
            self.log_callback(f"[{datetime.now().strftime('%H:%M:%S')}] Connected to MCP server at {HOST}:{PORT}")
            while self.running:
                start = time.time()
                data = self.sock.recv(65536)
                if not data:
                    break
                elapsed = time.time() - start
                try:
                    msg = data.decode('utf-8')
                    resp = json.loads(msg)
                    status = resp.get('status', '')
                    if status == 'ok':
                        self.success_count += 1
                    else:
                        self.error_count += 1
                        self.error_callback(msg)
                    self.latencies.append(elapsed)
                    self.log_callback(f"[{datetime.now().strftime('%H:%M:%S')}] RESPONSE: {msg}")
                    self.stats_callback(self.success_count, self.error_count, self.latencies)
                except Exception as e:
                    self.error_count += 1
                    self.error_callback(f"Decode error: {e}\nRaw: {data}")
                    self.stats_callback(self.success_count, self.error_count, self.latencies)
        except Exception as e:
            self.error_callback(f"Connection error: {e}")
        finally:
            if self.sock:
                self.sock.close()

    def stop(self):
        self.running = False
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except:
                pass

class SignalBus(QObject):
    log_signal = Signal(str)
    error_signal = Signal(str)
    stats_signal = Signal(int, int, list)

class MCPDebugUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MCP Debugger")
        self.resize(1000, 700)
        layout = QVBoxLayout(self)

        # Overview panel
        self.stats_label = QLabel("Success: 0 | Errors: 0 | Avg Latency: 0 ms")
        layout.addWidget(self.stats_label)

        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        # Log panel
        log_frame = QFrame()
        log_layout = QVBoxLayout(log_frame)
        log_layout.addWidget(QLabel("Live Log (Commands & Responses):"))
        self.log_list = QListWidget()
        log_layout.addWidget(self.log_list, 1)
        splitter.addWidget(log_frame)

        # Error/detail panel
        error_frame = QFrame()
        error_layout = QVBoxLayout(error_frame)
        error_layout.addWidget(QLabel("Errors / Details:"))
        self.error_list = QListWidget()
        error_layout.addWidget(self.error_list, 1)
        splitter.addWidget(error_frame)

        splitter.setSizes([700, 300])

        # Signal bus for thread-safe UI updates
        self.bus = SignalBus()
        self.bus.log_signal.connect(self.add_log)
        self.bus.error_signal.connect(self.add_error)
        self.bus.stats_signal.connect(self.update_stats)

        # Start MCP client thread
        self.client_thread = MCPClientThread(
            log_callback=lambda msg: self.bus.log_signal.emit(msg),
            error_callback=lambda msg: self.bus.error_signal.emit(msg),
            stats_callback=lambda s, e, l: self.bus.stats_signal.emit(s, e, l)
        )
        self.client_thread.start()

    def add_log(self, msg):
        item = QListWidgetItem(msg)
        self.log_list.addItem(item)
        self.log_list.scrollToBottom()

    def add_error(self, msg):
        item = QListWidgetItem(msg)
        item.setForeground(Qt.red)
        self.error_list.addItem(item)
        self.error_list.scrollToBottom()

    def update_stats(self, success, errors, latencies):
        avg_latency = (sum(latencies) / len(latencies) * 1000) if latencies else 0
        self.stats_label.setText(f"Success: {success} | Errors: {errors} | Avg Latency: {avg_latency:.1f} ms")

    def closeEvent(self, event):
        self.client_thread.stop()
        super().closeEvent(event)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MCPDebugUI()
    win.show()
    sys.exit(app.exec()) 