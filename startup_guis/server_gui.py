#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

# Import Qt through qt_compat so this GUI can use PyQt6 normally and PyQt5 on
# legacy Windows 10 systems that cannot load Qt6.
from startup_guis.qt_compat import (  # noqa: E402
    HORIZONTAL,
    NO_WRAP,
    VERTICAL,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    app_exec,
)
from startup_guis.shared import BODY_FONT, CONFIG_DIR, GENERATED_CONFIG_DIR, SECTION_FONT, TEXT_FONT, TITLE_FONT, ManagedCommand, action_button, append_terminal_text, configure_terminal, load_yaml, write_yaml, yaml_text  # noqa: E402


DEFAULT_CONFIG_PATH = CONFIG_DIR / 'Spectra300.yaml'
GENERATED_CONFIG_PATH = GENERATED_CONFIG_DIR / 'server_gui.yaml'
DEVICE_MODULES = {
    'camera': 'asyncroscopy.instruments.electron_microscope.detectors.camera',
    'corrector': 'asyncroscopy.instruments.electron_microscope.hardware.corrector',
    'data': 'asyncroscopy.data.data',
    'eds': 'asyncroscopy.instruments.electron_microscope.detectors.eds',
    'flucam': 'asyncroscopy.instruments.electron_microscope.detectors.flucam',
    'scan': 'asyncroscopy.instruments.electron_microscope.hardware.scan',
    'stage': 'asyncroscopy.instruments.electron_microscope.hardware.stage',
}


def server_config_from_values(values: dict) -> dict:
    devices = {key: spec for key, spec in values['devices'].items() if values['enabled_devices'][key]}
    microscope = dict(values['microscope'])
    if values['autoscript_host']:
        microscope['host'] = values['autoscript_host']
    if values['autoscript_port']:
        microscope['port'] = int(values['autoscript_port'])
    return {
        'microscope': microscope,
        'digital_twin': dict(values['digital_twin']),
        'devices': devices,
        'tango': {
            'host': values['tango_host'],
            'port': int(values['tango_port']),
            'reset_database_file': values['reset_database_file'],
        },
        'tiled': {
            'host': values['tiled_host'],
            'port': int(values['tiled_port']),
            'acquisition_dir': values['acquisition_dir'],
            'autostart': values['tiled_autostart'],
        },
        'device_timeout_seconds': int(values['device_timeout_seconds']),
    }


class ServerGui(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Asyncroscopy Server Startup')
        self.resize(1180, 760)
        self.command = ManagedCommand(self.enqueue_output, self.process_done)
        self.default_config = load_yaml(DEFAULT_CONFIG_PATH)
        self.device_config = self.default_config.get('devices', {})
        self.inputs: dict[str, QLineEdit | QComboBox | QCheckBox] = {}
        self.device_checks: dict[str, QCheckBox] = {}
        self.build()
        self.refresh_yaml()

    def build(self) -> None:
        self.setFont(BODY_FONT)
        root = QSplitter(VERTICAL)
        top = QSplitter(HORIZONTAL)
        controls = QWidget()
        preview = QWidget()
        terminal = QWidget()
        root.addWidget(top)
        root.addWidget(terminal)
        top.addWidget(controls)
        top.addWidget(preview)
        root.setSizes([520, 240])
        top.setSizes([520, 640])
        self.setCentralWidget(root)

        self.build_controls(controls)
        self.build_preview(preview)
        self.build_terminal(terminal)

    def build_controls(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        title = QLabel('Asyncroscopy Server Startup')
        title.setFont(TITLE_FONT)
        layout.addWidget(title)

        database = self.section('Database')
        self.add_row(database, 'Tango host', self.line_input('tango_host', self.default_config['tango'].get('host', 'localhost')))
        self.add_row(database, 'Tango port', self.line_input('tango_port', self.default_config['tango'].get('port', 9094)))
        reset_database = self.check_input('reset_database_file', 'Delete tango_database.db before start', bool(self.default_config['tango'].get('reset_database_file', False)))
        database.layout().addRow('', reset_database)
        layout.addWidget(database)

        microscope = self.section('Microscope')
        mode = QComboBox()
        mode.addItems(['real', 'dt'])
        mode.currentTextChanged.connect(self.refresh_yaml)
        self.inputs['microscope_mode'] = mode
        self.add_row(microscope, 'Mode', mode)
        default_microscope = self.default_config['microscope']
        self.add_row(microscope, 'AutoScript host', self.line_input('autoscript_host', default_microscope.get('host', '')))
        self.add_row(microscope, 'AutoScript port', self.line_input('autoscript_port', default_microscope.get('port', 9095)))
        self.add_row(microscope, 'Device timeout', self.line_input('device_timeout_seconds', self.default_config.get('device_timeout_seconds', 120)))
        layout.addWidget(microscope)

        tiled = self.default_config['tiled']
        data_server = self.section('Data server')
        self.add_row(data_server, 'Tiled host', self.line_input('tiled_host', tiled.get('host', 'localhost')))
        self.add_row(data_server, 'Tiled port', self.line_input('tiled_port', tiled.get('port', 9091)))
        self.add_row(data_server, 'Acquisition dir', self.line_input('acquisition_dir', tiled.get('acquisition_dir', 'outputs/tiled_acquisitions')))
        autostart = self.check_input('tiled_autostart', 'Start Tiled HTTP server', bool(tiled.get('autostart', True)))
        data_server.layout().addRow('', autostart)
        layout.addWidget(data_server)

        devices = QGroupBox('Devices')
        devices.setFont(SECTION_FONT)
        device_grid = QGridLayout(devices)
        for index, key in enumerate(DEVICE_MODULES):
            checkbox = QCheckBox(key)
            checkbox.setChecked(key in self.device_config)
            checkbox.stateChanged.connect(self.refresh_yaml)
            self.device_checks[key] = checkbox
            device_grid.addWidget(checkbox, index // 2, index % 2)
        layout.addWidget(devices)

        actions = QHBoxLayout()
        start = action_button('Start', '#1f7a35', '#2ea043')
        stop = action_button('Stop', '#b42318', '#dc2626')
        load = QPushButton('Load config file')
        save = QPushButton('Save current config')
        start.clicked.connect(self.start)
        stop.clicked.connect(self.command.stop)
        load.clicked.connect(self.read_config)
        save.clicked.connect(self.save_config)
        for button in (start, stop, load, save):
            button.setFont(BODY_FONT)
            actions.addWidget(button)
        layout.addLayout(actions)
        layout.addStretch()

    def build_preview(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        label = QLabel('Configuration (.yaml)')
        label.setFont(SECTION_FONT)
        layout.addWidget(label)
        self.yaml_preview = QTextEdit()
        self.yaml_preview.setFont(TEXT_FONT)
        self.yaml_preview.setReadOnly(True)
        self.yaml_preview.setLineWrapMode(NO_WRAP)
        layout.addWidget(self.yaml_preview)

    def build_terminal(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        label = QLabel('Terminal output')
        label.setFont(SECTION_FONT)
        layout.addWidget(label)
        self.output = QTextEdit()
        configure_terminal(self.output)
        layout.addWidget(self.output)

    def section(self, title: str) -> QGroupBox:
        group = QGroupBox(title)
        group.setFont(SECTION_FONT)
        group.setLayout(QFormLayout())
        return group

    def add_row(self, group: QGroupBox, label: str, widget: QWidget) -> None:
        group.layout().addRow(label, widget)

    def line_input(self, key: str, value) -> QLineEdit:
        widget = QLineEdit(str(value))
        widget.textChanged.connect(self.refresh_yaml)
        self.inputs[key] = widget
        return widget

    def check_input(self, key: str, label: str, checked: bool) -> QCheckBox:
        widget = QCheckBox(label)
        widget.setChecked(checked)
        widget.stateChanged.connect(self.refresh_yaml)
        self.inputs[key] = widget
        return widget

    def current_config(self) -> dict:
        values = {
            'microscope_mode': self.inputs['microscope_mode'].currentText(),
            'autoscript_host': self.inputs['autoscript_host'].text(),
            'autoscript_port': self.inputs['autoscript_port'].text(),
            'tango_host': self.inputs['tango_host'].text(),
            'tango_port': self.inputs['tango_port'].text(),
            'reset_database_file': self.inputs['reset_database_file'].isChecked(),
            'tiled_host': self.inputs['tiled_host'].text(),
            'tiled_port': self.inputs['tiled_port'].text(),
            'acquisition_dir': self.inputs['acquisition_dir'].text(),
            'tiled_autostart': self.inputs['tiled_autostart'].isChecked(),
            'device_timeout_seconds': self.inputs['device_timeout_seconds'].text(),
            'enabled_devices': {key: checkbox.isChecked() for key, checkbox in self.device_checks.items()},
            'devices': {key: self.device_config.get(key, {'module_name': DEVICE_MODULES[key]}) for key in DEVICE_MODULES},
            'microscope': self.default_config['microscope'],
            'digital_twin': self.default_config.get('digital_twin', {}),
        }
        return server_config_from_values(values)

    def refresh_yaml(self) -> None:
        if hasattr(self, 'yaml_preview'):
            self.yaml_preview.setPlainText(yaml_text(self.current_config()))

    def save_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, 'Save config', str(CONFIG_DIR / 'server_config.yaml'), 'YAML (*.yaml *.yml);;All files (*)')
        if path:
            write_yaml(Path(path), self.current_config())
            self.enqueue_output(f'Saved {path}\n')

    def read_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Load config', str(CONFIG_DIR), 'YAML (*.yaml *.yml);;All files (*)')
        if not path:
            return
        config = load_yaml(Path(path))
        self.default_config = config
        self.device_config = config.get('devices', {})
        microscope = config.get('microscope', {})
        tango = config.get('tango', {})
        tiled = config.get('tiled', {})
        self.inputs['autoscript_host'].setText(str(microscope.get('host', '')))
        self.inputs['autoscript_port'].setText(str(microscope.get('port', '')))
        self.inputs['tango_host'].setText(str(tango.get('host', 'localhost')))
        self.inputs['tango_port'].setText(str(tango.get('port', 9094)))
        self.inputs['reset_database_file'].setChecked(bool(tango.get('reset_database_file', False)))
        self.inputs['tiled_host'].setText(str(tiled.get('host', 'localhost')))
        self.inputs['tiled_port'].setText(str(tiled.get('port', 9091)))
        self.inputs['acquisition_dir'].setText(tiled.get('acquisition_dir', 'outputs/tiled_acquisitions'))
        self.inputs['tiled_autostart'].setChecked(bool(tiled.get('autostart', True)))
        self.inputs['device_timeout_seconds'].setText(str(config.get('device_timeout_seconds', 120)))
        for key, checkbox in self.device_checks.items():
            checkbox.setChecked(key in self.device_config)
        self.refresh_yaml()
        self.enqueue_output(f'Loaded {path}\n')

    def start(self) -> None:
        config_path = write_yaml(GENERATED_CONFIG_PATH, self.current_config())
        self.command.start(['uv', 'run', 'python', '-u', 'startup_scripts/run_servers.py', '--yaml', str(config_path), '--microscope', self.inputs['microscope_mode'].currentText()])

    def enqueue_output(self, text: str) -> None:
        append_terminal_text(self.output, text)

    def process_done(self, returncode: int | None) -> None:
        self.enqueue_output(f'\nProcess exited with return code {returncode}.\n')


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = ServerGui()
    window.show()
    sys.exit(app_exec(app))
