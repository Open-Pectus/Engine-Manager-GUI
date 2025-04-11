"""Run multiple [Open Pectus](https://github.com/Open-Pectus/Open-Pectus/)
engines in a convenient user interface."""
import sys
INITIAL_MODULES = sys.modules.keys()
import asyncio
from collections import defaultdict
import ctypes
import json
import logging
import os
import platform
import ssl
import threading
import time
import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk
import tkinter.font
from typing import Callable
import webbrowser
from concurrent.futures import Future

from filelock import FileLock
import httpx
import pystray
import multiprocess
import multiprocess.spawn
from openpectus.engine.engine import Engine
from openpectus.engine.engine_runner import EngineRunner

__version__ = "0.1.0"
# This application is written for Windows
assert platform.system() == "Windows"
import_lock = threading.Lock()
# Initialize log directory
log_directory = os.path.join(
    os.path.expanduser("~"),
    "AppData",
    "Local",
    "OpenPectusEngineManagerGui",
    "logs",
)
os.makedirs(log_directory, exist_ok=True)
# Set up SSL context to use Windows certificate store
ssl_context = ssl.create_default_context()
ssl_context.load_default_certs()

log = logging.getLogger("openpectus.engine_manager_gui")


class JsonData:
    """
    A dict stand-in which saves as a JSON file on write.

    Args:
        filename:
            Filename to save JSON representation to.
        data:
            Default data used to initialize if filename
            does not exist.
    """
    filename: str
    data: dict

    def __init__(self):
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)
        self._lock = FileLock(self.filename+".lock")
        self.read()
        self.write({})

    def read(self):
        with self._lock:
            try:
                with open(self.filename, "r") as f:
                    loaded_data = json.load(f)
                    self.data.update(loaded_data)
            except (json.JSONDecodeError, IOError):
                pass

    def write(self, payload: dict[str, str | int | float | bool]):
        with self._lock:
            self.read()
            for k, v in payload.items():
                self.data[k] = v
            with open(self.filename, "w") as f:
                json.dump(self.data, f, indent=2)

    def __getitem__(self, k: str):
        self.read()
        return self.data[k]

    def __setitem__(self, k: str, v: str | int | float | bool):
        self.write({k: v})

    def dict(self):
        self.read()
        return self.data


class PersistentData(JsonData):
    filename = os.path.join(
        os.path.expanduser("~"),
        "AppData",
        "Local",
        "OpenPectusEngineManagerGui",
        "config.json"
    )
    data = {
        "aggregator_hostname": "openpectus.com",
        "aggregator_port": 443,
        "aggregator_secure": True,
        "aggregator_secret": "",
        "uods": []
    }


class LogRecorder(logging.Handler):
    """
    Loggin handler which accumulates log records from threads
    other than the main thread. Callbacks can be attached to
    execute a function when a log record is recorded.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logs: dict[str, list[logging.LogRecord]] = defaultdict(list)
        self.emit_callbacks: list[Callable] = []
        self.engine_names: list[str] = []

    def emit(self, record: logging.LogRecord):
        assert record.threadName is not None
        if record.threadName in self.engine_names:
            self.logs[record.threadName].append(record)
            for fn in self.emit_callbacks:
                fn(record, record.threadName)

    def clear_log(self, thread_name):
        if thread_name in self.logs:
            del self.logs[thread_name]


class EngineManager:
    """
    Starts, stops and validates engines in background threads.
    Engine status is reported to GUI by overriding
    set_status_for_item method.

    Args:
        log_handler:
            Logging handler taking care of openpectus logs.
        persistent_data:
            Dict-like object storing persistent data.
    """
    def __init__(self, log_handler: logging.Handler, persistent_data):
        self.log_handler = log_handler
        self.persistent_data = persistent_data
        # Internal state
        self.engines: dict[str, tuple[Engine, EngineRunner]] = dict()
        self.threads: dict[str, threading.Thread] = dict()
        self.loops: dict[str, asyncio.AbstractEventLoop] = dict()
        self._tasks: set[Future] = set()
        self.running_engines_names: set[str] = set()

    def set_status_for_item(self, status, item):
        raise NotImplementedError

    def start_engine(self, engine_item: dict[str, str]):
        log.info(f"Starting engine in engine manager {engine_item}")
        engine_name = engine_item["engine_name"]
        uod_filename = engine_item["filename"]

        async def run_engine(loop: asyncio.EventLoop):
            """Condensed version of main_async from openpectus.engine.main"""
            # Imported modules are cached in sys.modules. This causes issues
            # because variables defined in a module are shared across all
            # threads using that module.
            # Remove cached modules to avoid cross-contamination between
            # engine instances. "sys" variable is shared among threads,
            # so don't let threads manipulate it at the same time.
            import_lock.acquire()
            for k in list(sys.modules.keys()):
                if k not in INITIAL_MODULES:
                    del sys.modules[k]
            from openpectus.engine.main import create_uod
            from openpectus.engine.engine import Engine
            from openpectus.engine.hardware_recovery import (
                ErrorRecoveryConfig,
                ErrorRecoveryDecorator,
            )
            from openpectus.engine.engine_message_handlers import EngineMessageHandlers
            from openpectus.engine.engine_message_builder import EngineMessageBuilder
            from openpectus.engine.engine_runner import EngineRunner
            from openpectus.protocol.engine_dispatcher import EngineDispatcher
            from openpectus.lang.exec.tags import SystemTagName
            import logging
            import os
            from logging.handlers import RotatingFileHandler
            import_lock.release()
            # Attach log recorder to Open Pectus loggers created on import
            # to catch them and show them in EngineOutput
            file_log_path = os.path.join(
                log_directory,
                f"{engine_name}-openpectus-engine.log"
            )
            file_handler = RotatingFileHandler(
                file_log_path,
                maxBytes=2*1024*1024,
                backupCount=5
            )
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s]: %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S"
                )
            )

            loggerDict = logging.root.manager.loggerDict
            for name in list(loggerDict.keys()):
                if name.startswith("openpectus"):
                    if isinstance(loggerDict[name], logging.Logger):
                        logging.getLogger(name).addHandler(self.log_handler)
                        logging.getLogger(name).addHandler(file_handler)
            # Actually start engine
            try:
                uod = create_uod(uod_filename)
            except Exception as ex:
                log.error(f"Failed to create uod: {ex}")
                return
            engine = Engine(uod, enable_archiver=True)
            dispatcher = EngineDispatcher(f"{self.persistent_data['aggregator_hostname']}:{self.persistent_data['aggregator_port']}", self.persistent_data['aggregator_secure'], uod.options, self.persistent_data["aggregator_secret"])
            if len(uod.required_roles) > 0 and not dispatcher.is_aggregator_authentication_enabled():
                log.warning('"with_required_roles" specified in ' +
                            f'"{uod_filename}" but aggregator does ' +
                            'not support authentication. Engine will not ' +
                            'be visible in the frontend.')
            try:
                log.info("Verifying hardware configuration and connection")
                uod.validate_configuration()
                uod.hwl.validate_offline()
                uod.hwl.connect()
                uod.hwl.validate_online()
                log.info("Hardware validation successful")

                log.info("Building uod commands")
                uod.build_commands()
            except Exception:
                log.error("A hardware related error occurred. " +
                          "Engine cannot start.")
                return
            connection_status_tag = engine._system_tags[SystemTagName.CONNECTION_STATUS]
            uod.hwl = ErrorRecoveryDecorator(
                uod.hwl,
                ErrorRecoveryConfig(),
                connection_status_tag
            )
            message_builder = EngineMessageBuilder(engine)
            # create runner that orchestrates the error recovery mechanism
            runner = EngineRunner(
                dispatcher,
                message_builder,
                engine.emitter,
                loop
            )
            _ = EngineMessageHandlers(engine, dispatcher)

            async def on_steady_state():
                assert engine is not None
                engine.run()

            runner.first_steady_state_callback = on_steady_state
            self.engines[engine_name] = (engine, runner)
            await runner.run()
            file_handler.close()

        self.loops[engine_name] = asyncio.new_event_loop()

        def run_engine_task():
            async def async_task(loop):
                await run_engine(loop)
                self.set_status_for_item("Not running", engine_item)
                self.loops[engine_name].stop()
                log.info(f"Finished running {engine_name}")

            self.loops[engine_name].run_until_complete(async_task(self.loops[engine_name]))
            self.loops[engine_name].close()

        self.threads[engine_name] = threading.Thread(
            name=engine_name,
            target=run_engine_task,
            daemon=True,
        )
        self.threads[engine_name].start()
        self.running_engines_names.add(engine_name)

    def stop_engine(self, engine_item: dict[str, str]):
        assert engine_item["engine_name"] in self.loops
        engine_name = engine_item["engine_name"]

        async def cancel():
            """Mimics close_async in openpectus.engine.main"""
            log.info(f"Stopping {engine_name}")
            if engine_name in self.engines:
                engine, runner = self.engines[engine_name]
                engine.stop()
                await runner.shutdown()
            log.info(f"Stopped {engine_name}")

        if self.loops[engine_name].is_running():
            task = asyncio.run_coroutine_threadsafe(
                cancel(),
                self.loops[engine_name]
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            task.add_done_callback(lambda _: self.running_engines_names.discard(engine_name))

    def validate_engine(self, engine_item: dict[str, str]):
        engine_name = engine_item["engine_name"]
        uod_filename = engine_item["filename"]

        def validate():
            # Remove cached modules to avoid cross-contamination
            import_lock.acquire()
            for k in list(sys.modules.keys()).copy():
                if k not in INITIAL_MODULES:
                    del sys.modules[k]
            from openpectus.engine.main import validate_and_exit
            import logging
            import os
            from logging.handlers import RotatingFileHandler
            import_lock.release()
            # Attach log recorder to Open Pectus logs to catch them
            # and show them in EngineOutput
            file_log_path = os.path.join(
                log_directory,
                f"{engine_name}-openpectus-engine.log",
            )
            file_handler = RotatingFileHandler(
                file_log_path,
                maxBytes=2*1024*1024,
                backupCount=5,
            )
            file_handler.setLevel(logging.INFO)
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s]: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            ))
            loggerDict = logging.root.manager.loggerDict
            for name in list(loggerDict.keys()):
                if name.startswith("openpectus"):
                    if isinstance(loggerDict[name], logging.Logger):
                        logging.getLogger(name).addHandler(self.log_handler)
                        logging.getLogger(name).addHandler(file_handler)
            try:
                validate_and_exit(uod_filename)
            except SystemExit:
                pass
            self.set_status_for_item("Not running", engine_item)
        self.threads[engine_name] = threading.Thread(
            name=engine_name,
            target=validate,
            daemon=True
        )
        self.threads[engine_name].start()

    def get_running_engines(self) -> list[str]:
        running_engines = []
        for engine_name, loop in self.loops.items():
            if loop.is_running():
                running_engines.append(engine_name)
        return running_engines

    def stop_all_running_engines(self):
        for engine_name in self.running_engines_names:
            engine_item = dict(engine_name=engine_name)
            self.stop_engine(engine_item)


class VerticalScrolledZoomableLockedText(ttk.Frame):
    """
    Scrollable, zoomable frame for text widgets.

    Args:
        parent:
            Tk widget holding this frame.
        Text:
            Class from which to instantiate text object.
        args:
            Positional arguments passed on to Text class.
        args:
            Keyword arguments passed on to Text class.
    """
    def __init__(self, parent, Text, *args, **kwargs):
        super().__init__(parent)
        df = tkinter.font.nametofont("TkDefaultFont")
        kwargs["font"] = ("Courier New", df.cget("size"),)
        text = Text(self, *args, **kwargs)
        sb = tk.Scrollbar(self, orient=tk.VERTICAL)
        text.config(yscrollcommand=sb.set)
        sb.config(command=text.yview)

        sb.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.BOTH, expand=True)
        self.text = text

        self.text.bind("<Control-MouseWheel>", self._zoom)
        self.text.bind("<Control-plus>", self._zoom_in)
        self.text.bind("<Control-minus>", self._zoom_out)

        # Set up _proxy to enforce no-edit
        self._orig = text._w + "_orig"
        text.tk.call("rename", text._w, self._orig)
        text.tk.createcommand(text._w, self._proxy)

    def _proxy(self, *args):
        """Step in between modifications to the contained text to
        enforce no-edit."""
        if args[0] == "delete" and args[1:] != ("1.0", tk.END,):
            return
        elif args[0] == "insert" and args[1] != tk.END:
            return
        cmd = (self._orig,) + args
        result = self.tk.call(cmd)
        return result

    def _zoom(self, event: tk.Event):
        if event.delta > 0:
            self._zoom_in(event)
        else:
            self._zoom_out(event)

    def _zoom_in(self, event: tk.Event):
        # self.text.cget("font")) example: {Courier New} 12
        size = int(self.text.cget("font").rsplit(" ")[-1])
        self.text.configure(font=("Courier New", size+1,))

    def _zoom_out(self, event: tk.Event):
        size = int(self.text.cget("font").rsplit(" ")[-1])
        self.text.configure(font=("Courier New", size-1,))


class SingletonWindow:
    """
    Class which makes sure that a Tk Window is only
    presented once.
    """
    def __init__(self, parent):
        """
        Keep track of existence of a window.
        The window is created by start() and dismissed by exit()
        """
        self.exists = False
        self.parent = parent

    def _exit(self):
        """
        On-exit method that can be over-written by classes
        inheriting from this one.
        """
        pass

    def exit(self):
        """
        Close window and note that it no longer exists.
        """
        if self._exit() is not False:
            self.window.destroy()
            self.exists = False

    def start(self) -> bool:
        if self.exists:
            self.window.deiconify()  # Bring the window into view.
            return False
        self.window: tk.Toplevel = tk.Toplevel(self.parent)
        self.window.protocol("WM_DELETE_WINDOW", self.exit)
        self.exists = True
        return True


class SettingsWindow(SingletonWindow):
    """
    Window with input fields to specify aggregator information.
    """
    def __init__(self, *args, **kwargs):
        self.persistent_data = kwargs.pop("persistent_data")
        super().__init__(*args, **kwargs)

    def start(self):
        if not super().start():
            return False

        window = self.window
        window.title("Aggregator Settings")
        ag_ssl_value = tk.IntVar(value=1)

        # Create GUI elements
        label_ag_hostname = tk.Label(window, text="Aggregator Hostname")
        label_ag_port = tk.Label(window, text="Aggregator Port")
        label_ag_ssl = tk.Label(window, text="Aggregator SSL")
        label_ag_secret = tk.Label(window, text="Aggregator Secret")
        entry_ag_hostname = tk.Entry(window)
        entry_ag_port = tk.Entry(window)
        checkbox_ag_ssl = tk.Checkbutton(
            window,
            text="",
            variable=ag_ssl_value,
            onvalue=1,
            offvalue=0
        )
        entry_ag_secret = tk.Entry(window)
        verify_and_save_button = tk.Button(
            window,
            text="Verify and Save"
        )

        # Populate GUI elements with current values
        if self.persistent_data["aggregator_secure"]:
            checkbox_ag_ssl.select()
        entry_ag_hostname.insert(0, self.persistent_data["aggregator_hostname"])
        entry_ag_port.insert(0, self.persistent_data["aggregator_port"])
        entry_ag_secret.insert(0, self.persistent_data["aggregator_secret"])

        # Configure layout
        label_ag_hostname.grid(row=0, column=0, sticky=tk.W)
        entry_ag_hostname.grid(row=0, column=1)
        label_ag_port.grid(row=1, column=0, sticky=tk.W)
        entry_ag_port.grid(row=1, column=1)
        label_ag_ssl.grid(row=2, column=0, sticky=tk.W)
        checkbox_ag_ssl.grid(row=2, column=1)
        label_ag_secret.grid(row=3, column=0, sticky=tk.W)
        entry_ag_secret.grid(row=3, column=1)
        verify_and_save_button.grid(row=4, column=0, columnspan=2)

        def reset_button():
            verify_and_save_button["bg"] = "SystemButtonFace"

        def verify_connection_and_save():
            reset_button()
            http_schema = "https" if ag_ssl_value.get() else "http"
            health_url = "".join([
                http_schema,
                "://",
                entry_ag_hostname.get(),
                ":",
                entry_ag_port.get(),
                "/health",
            ])
            try:
                httpx.get(
                    health_url,
                    headers={"User-Agent": "Open-Pectus-Gui"},
                    timeout=1,
                    verify=ssl_context,
                )
                verify_and_save_button["bg"] = "#8fff9c"
                self.persistent_data.write(dict(
                    aggregator_hostname=entry_ag_hostname.get(),
                    aggregator_port=entry_ag_port.get(),
                    aggregator_secure=ag_ssl_value.get() == 1,
                    aggregator_secret=entry_ag_secret.get(),
                ))
            except httpx.HTTPError:
                # Blink button red
                verify_and_save_button["bg"] = "#ff8f8f"
                self.parent.after(500, reset_button)

        verify_and_save_button.configure(command=verify_connection_and_save)
        return True


class EngineListPanel(ttk.LabelFrame):
    """
    List of engines.
    Right click to start, validate and stop
    running engines.
    """
    def __init__(self, master, *args, **kwargs):
        self.master = master
        super().__init__(*args, **kwargs)

        self.configure(text="Engine List")

        # Internal state
        self.engine_name_to_row_id = dict()
        self.engine_name_to_tag = defaultdict(lambda: "INFO")

        # Create GUI elements
        treeview = ttk.Treeview(self, columns=("Status",))
        treeview.heading("#0", text="Filename")
        treeview.heading("Status", text="Status")
        treeview.column("#0", stretch=True)
        treeview.column("Status", width=80, stretch=False)
        treeview.tag_configure("ERROR", background="#eed1d1")
        treeview.tag_configure("WARNING", background="#eeeed1")
        self.popup_menu = tk.Menu(master, tearoff=0)

        # Configure layout
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        treeview.grid(row=0, column=0, sticky=tk.N+tk.S+tk.E+tk.W)

        # Bind callbacks
        treeview.bind("<ButtonRelease-1>", self._on_select_item)
        treeview.bind("<KeyRelease-Up>", self._on_select_item)
        treeview.bind("<KeyRelease-Down>", self._on_select_item)
        treeview.bind("<ButtonRelease-3>", self._on_right_click)
        treeview.bind("<Delete>", self._on_delete)
        treeview.bind('<Control-a>', self._on_select_all)
        treeview.bind("<Control-s>", self._save_as)
        self.treeview = treeview

        # Callback endpoints
        self.select_item_callback: list[Callable] = []
        self.on_start_callback: list[Callable] = []
        self.on_stop_callback: list[Callable] = []
        self.on_restart_callback: list[Callable] = []
        self.on_validate_callback: list[Callable] = []

    def remove_uod(self, uod_filename: str):
        raise NotImplementedError

    def save_as(self, event: tk.Event):
        raise NotImplementedError

    def _save_as(self, event: tk.Event):
        self.save_as(event)

    def insert_item(self, filename: str = "", status: str = "Not running",):
        """Insert engine into list."""
        # Check if item already exists
        for row_id in self.treeview.get_children():
            item = self._get_item_by_id(row_id)
            if filename == item["filename"]:
                return
        # Insert item
        self.treeview.insert("", tk.END, text=filename, values=(status,))
        self.rebuild_engine_name_to_row_id()

    def set_status_for_item(self, status: str, item: dict[str, str]):
        self.treeview.item(
            self.engine_name_to_row_id[item["engine_name"]],
            values=(status,)
        )

    def set_tag_for_engine_name(self,
                                record: logging.LogRecord,
                                engine_name: str):
        tag = record.levelname
        if tag == "INFO":
            return
        if self.engine_name_to_tag[engine_name] == "ERROR":
            return
        if self.engine_name_to_tag[engine_name] == tag:
            return
        focus_id = self.treeview.focus()
        if focus_id:
            item = self._get_item_by_id(focus_id)
            if engine_name == item["engine_name"]:
                return

        self.treeview.item(
            self.engine_name_to_row_id[engine_name],
            tags=tag,
        )
        self.engine_name_to_tag[engine_name] = tag

    def get_all_items(self) -> list[dict[str, str]]:
        items = []
        for row_id in self.treeview.get_children():
            items.append(self._get_item_by_id(row_id))
        return items

    def _on_select_all(self, event: tk.Event):
        item_ids = self.treeview.get_children()
        if item_ids:
            self.treeview.selection_set(item_ids)
            for fn in self.select_item_callback:
                fn(self._get_item_by_id(item_ids[0]))

    def _on_right_click(self, event: tk.Event):
        treeview_item_ids = []
        selected_treeview_item_ids = self.treeview.selection()
        if len(self.treeview.selection()) < 2:
            treeview_item_id = self.treeview.identify_row(event.y)
            if treeview_item_id:
                # mouse pointer over item
                self.treeview.selection_set(treeview_item_id)
                self._on_select_item(event)
                treeview_item_ids = [treeview_item_id]
        else:
            treeview_item_ids = [e for e in selected_treeview_item_ids if e != ""]

        # Clear any items in the menu
        self.popup_menu.delete(0, "end")
        items = []
        for treeview_item_id in treeview_item_ids:
            items.append(self._get_item_by_id(treeview_item_id))
        for label, function_handle in self._populate_right_click_menu(items).items():
            self.popup_menu.add_command(label=label, command=function_handle)
        # Place the menu on screen
        self.popup_menu.post(event.x_root, event.y_root)

    def rebuild_engine_name_to_row_id(self):
        self.engine_name_to_row_id = dict()
        for row_id in self.treeview.get_children():
            item = self._get_item_by_id(row_id)
            self.engine_name_to_row_id[item["engine_name"]] = row_id

    def load_engine(self):
        raise NotImplementedError

    def _get_item_by_id(self, row_id: str) -> dict[str, str]:
        treeview_item = self.treeview.item(row_id)
        return dict(
            filename=treeview_item["text"],
            status=treeview_item["values"][0],
            engine_name=os.path.splitext(
                os.path.basename(treeview_item["text"])
                )[0],
        )

    def _on_select_item(self, event: tk.Event):
        """Callback attached to up/down key and left/right click."""
        # Check if source of event is keyboard
        if event.keycode == "??":
            treeview_item_id = self.treeview.identify_row(event.y)
            if not treeview_item_id:
                self.treeview.selection_set([])
                for fn in self.select_item_callback:
                    fn(None)
        else:
            treeview_item_id = self.treeview.focus()
        if not treeview_item_id:
            return
        item = self._get_item_by_id(treeview_item_id)
        # Reset color tag
        self.treeview.item(
            treeview_item_id,
            tags="",
        )
        self.engine_name_to_tag[item["engine_name"]] = "INFO"
        for fn in self.select_item_callback:
            fn(item)

    def _on_delete(self, event: tk.Event):
        for tree_view_item_id in self.treeview.selection():
            item = self._get_item_by_id(tree_view_item_id)
            if item["status"] == "Not running":
                self.treeview.delete(tree_view_item_id)
                self.remove_uod(item["filename"])
        self.rebuild_engine_name_to_row_id()

    def _populate_right_click_menu(self, items: list[dict[str, str]]) -> dict[str, Callable]:
        """Creates menu items in right click menu."""
        if len(items) > 1:
            if all([item["status"] == "Running" for item in items]):
                return {
                    "Restart engines": lambda: self._right_click_menu_restart_engine(items),
                    "Stop engines": lambda: self._right_click_menu_stop_engine(items),
                }
            elif all([item["status"] == "Not running" for item in items]):
                return {
                    "Start engines": lambda: self._right_click_menu_start_engine(items),
                    "Validate engine UODs": lambda: self._right_click_menu_validate_engine(items),
                    "Remove engines from list": lambda: self._right_click_menu_remove_uod_from_list(items),
                }
        elif len(items) == 1:
            item = items[0]
            if item["status"] == "Running":
                return {
                    f"Restart {item['engine_name']}": lambda: self._right_click_menu_restart_engine(items),
                    f"Stop {item['engine_name']}": lambda: self._right_click_menu_stop_engine(items),
                }
            elif item["status"] == "Not running":
                return {
                    f"Start {item['engine_name']}": lambda: self._right_click_menu_start_engine(items),
                    f"Validate {item['engine_name']} UOD": lambda: self._right_click_menu_validate_engine(items),
                    f"Remove {item['engine_name']} from list": lambda: self._right_click_menu_remove_uod_from_list(items),
                }
        elif len(items) == 0:
            return {
                "Load UOD": self.load_engine,
            }
        return dict()

    def _right_click_menu_start_engine(self, items):
        for item in items:
            if not os.path.isfile(item["filename"]):
                messagebox.showerror(
                    "UOD file does not exist",
                    f'UOD file at "{item["filename"]}" does not exist. ' +
                    'The UOD will be removed from the list.'
                )
                self._right_click_menu_remove_uod_from_list([item])
                continue
            self.set_status_for_item("Running", item)
            for fn in self.on_start_callback:
                fn(item)

    def _right_click_menu_validate_engine(self, items):
        for item in items:
            if not os.path.isfile(item["filename"]):
                messagebox.showerror(
                    "UOD file does not exist",
                    f'UOD file at "{item["filename"]}" does not exist. ' +
                    'The UOD will be removed from the list.'
                )
                self._right_click_menu_remove_uod_from_list([item])
                continue
            self.set_status_for_item("Validating...", item)
            for fn in self.on_validate_callback:
                fn(item)

    def _right_click_menu_remove_uod_from_list(self, items):
        for item in items:
            assert item["status"] == "Not running"
            self.treeview.delete(self.engine_name_to_row_id[item["engine_name"]])
            self.rebuild_engine_name_to_row_id()
            self.remove_uod(item["filename"])

    def _right_click_menu_stop_engine(self, items):
        for item in items:
            self.set_status_for_item("Stopping...", item)
            for fn in self.on_stop_callback:
                fn(item)

    def _right_click_menu_restart_engine(self, items):
        self._right_click_menu_stop_engine(items)
        self.master.after(100, self._attempt_restart, items)

    def _attempt_restart(self, selected_items):
        selected_filenames = [item["filename"] for item in selected_items]
        selected_items_updated = [item for item in self.get_all_items() if item["filename"] in selected_filenames]

        if all(item["status"] == "Not running" for item in selected_items_updated):
            self._right_click_menu_start_engine(selected_items_updated)
        else:
            self.master.after(100, self._attempt_restart, selected_items_updated)


class EngineOutput(tk.LabelFrame):
    """Text area with coloring of log statements
    based on severity. One text area is created
    and kept for each engine for faster rendering."""
    def __init__(self, master, *args, **kwargs):
        self.master = master
        super().__init__(*args, **kwargs)

        self.configure(text="Engine Output")

        # Configure layout
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # Internal state
        self.engine_name = None
        self.text = dict()
        self.text_areas: dict[str | None, VerticalScrolledZoomableLockedText] = dict()

        self.create_text_area(self.engine_name)
        self.text_areas[self.engine_name].grid(
            row=0,
            column=0,
            sticky=tk.N+tk.S+tk.E+tk.W
        )

        self.formatter = logging.Formatter(
            "%(asctime)s [%(name)s:%(levelname)s]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    def create_text_area(self, engine_name: None | str):
        if engine_name in self.text:
            assert engine_name in self.text_areas
            return
        text_area = VerticalScrolledZoomableLockedText(
            self,
            tk.Text,
            height=30,
            takefocus=False,
            insertontime=0,
        )

        text_area.text.bind("<Control-s>", self.save_as)

        text_area.text.tag_config("INFO", foreground="black")
        text_area.text.tag_config("DEBUG", foreground="grey")
        text_area.text.tag_config("WARNING", foreground="orange")
        text_area.text.tag_config("ERROR", foreground="red")
        text_area.text.tag_config("CRITICAL", foreground="red", underline=1)

        self.text[engine_name] = text_area.text
        self.text_areas[engine_name] = text_area

    def clear_text(self, item: dict[str, str]):
        self.text[item["engine_name"]].delete("1.0", tk.END)

    def set_engine(self, engine_item: None | dict[str, str]):
        if engine_item is None:
            self.engine_name = None
            self.configure(text="Engine Output")
        else:
            if engine_item["engine_name"] == self.engine_name:
                return
            # Hide current text area
            for text_area in self.text_areas.values():
                text_area.grid_remove()
            # Set label
            self.engine_name = engine_item["engine_name"]
            self.configure(text=f"Engine Output: {self.engine_name}")
        # Create text area if it doesn't exist
        self.create_text_area(self.engine_name)
        # Show the "new" text area
        self.text_areas[self.engine_name].grid(
            row=0,
            column=0,
            sticky=tk.N+tk.S+tk.E+tk.W
        )

    def insert_log_record_for_engine(self,
                                     record: logging.LogRecord,
                                     engine_name: str):
        self.text[engine_name].insert(
            tk.END,
            self.formatter.format(record) + "\n",
            (record.levelname,)
        )

    def save_as(self, event: tk.Event):
        if self.engine_name is None:
            return
        file = filedialog.asksaveasfilename(
            title=f"Save log file for engine {self.engine_name}",
            filetypes=(("Log file", "*.txt"),),
            initialfile=f"{self.engine_name}-openpectus-engine.log",
        )
        if not file:
            return
        if not file.endswith(".txt"):
            file += ".txt"
        with open(file, "w") as f:
            f.write(self.text[self.engine_name].get("1.0", tk.END))


class OpenPectusEngineManagerGui(tk.Tk):
    def __init__(self, persistent_data):
        self.persistent_data = persistent_data
        super().__init__()

        # Hide window while GUI is assembled
        self.withdraw()

        # Set taskbar icon
        icon_path = os.path.join(
            os.path.dirname(__file__),
            "icon.png",
        )
        self.iconphoto(True, tk.PhotoImage(file=icon_path))

        # Create system tray icon
        self.protocol("WM_DELETE_WINDOW", self._close_window)
        tray_menu = (
            pystray.MenuItem("Show", self._show_from_tray, default=True),
            pystray.MenuItem("Exit", self._exit),
        )
        from PIL import Image
        self.icon = pystray.Icon(
            "name",
            Image.open(icon_path),
            "Open Pectus Engine Manager",
            tray_menu,
        )
        self.icon.run_detached()

        # Fix to make the icon on the Windows task bar the same
        # as that in the top left corner.
        # Source: https://stackoverflow.com/questions/14900510/
        aid = "openpectusenginemanager"  # arbitrary string
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aid)
        ctypes.windll.shcore.SetProcessDpiAwareness(1)

        # Create GUI elements
        self.title("Open Pectus Engine Manager")
        # Create windows
        settings_window = SettingsWindow(self, persistent_data=persistent_data)
        # Create panes
        paned_window_left = tk.PanedWindow(self)
        paned_window_right = tk.PanedWindow(
            paned_window_left,
            orient=tk.VERTICAL
        )
        engine_list = EngineListPanel(paned_window_left)
        engine_output = EngineOutput(paned_window_right)
        # Menu
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=0)
        file_menu.add_command(
            label="Aggregator Settings",
            command=settings_window.start
        )
        file_menu.add_command(
            label="Open Aggregator",
            command=self._open_aggregator
        )
        file_menu.add_command(
            label="Load UOD",
            command=self._load_engines
        )
        file_menu.add_command(
            label='Open log directory',
            command=lambda: os.system(f'explorer "{log_directory}"')
        )
        file_menu.add_command(
            label="About",
            command=self._about
        )
        file_menu.add_command(
            label="Exit",
            command=self._exit
        )
        # Add options to root menu
        menu.add_cascade(label="File", menu=file_menu)
        self.configure(menu=menu)

        # Configure layout
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        paned_window_left.grid(row=0, column=0, sticky=tk.N+tk.S+tk.E+tk.W)
        paned_window_left.add(engine_list)
        paned_window_left.add(paned_window_right, stretch="always")
        paned_window_right.add(engine_output, stretch="always")

        # Callback endpoints
        self.add_engine_callback: list[Callable] = []
        self.remove_engine_callback: list[Callable] = []

        # Bind callbacks
        engine_list.select_item_callback.append(engine_output.set_engine)
        engine_list.on_start_callback.append(engine_output.clear_text)
        engine_list.on_validate_callback.append(engine_output.clear_text)
        engine_list.remove_uod = self.remove_uod
        engine_list.save_as = engine_output.save_as

        # Set minimum size
        self.minsize(width=400, height=200)

        # Show window
        self.deiconify()

        # Internal state
        self._notification_message_time: float = 0.0
        self.engine_list = engine_list
        self.engine_output = engine_output

        # Focus on treeview
        engine_list.treeview.focus_set()

    def ask_before_exit(self) -> bool:
        """Override this method."""
        return False

    def stop_all_running_engines(self):
        """Override this method."""

    def load_uod_file(self, uod_filename: str) -> bool:
        log.info(f"Loading engine UOD {uod_filename}.")
        loaded_uods = [item["filename"] for item in self.engine_list.get_all_items()]
        if uod_filename in loaded_uods:
            return False
        self.engine_list.insert_item(filename=uod_filename)
        engine_name = os.path.splitext(
            os.path.basename(uod_filename)
        )[0]
        self.engine_output.create_text_area(engine_name)
        for fn in self.add_engine_callback:
            fn(engine_name)
        return True

    def remove_uod(self, uod_filename: str):
        engine_name = os.path.splitext(
            os.path.basename(uod_filename)
        )[0]
        uods = self.persistent_data["uods"]
        if uod_filename in uods:
            uods.remove(uod_filename)
        self.persistent_data["uods"] = uods
        for fn in self.remove_engine_callback:
            fn(engine_name)

    def _show_from_tray(self, icon, item):
        self.icon.remove_notification()
        self.after(0, self.deiconify)

    def _exit_when_all_stopped(self):
        if self.ask_before_exit():
            self.after(10, self._exit_when_all_stopped)
        else:
            self.icon.stop()
            self.after(0, self.destroy)

    def _exit(self, *args):
        if self.ask_before_exit():
            answer = messagebox.askquestion(
                "Exit",
                "Engines are still running. Do you wish to exit?"
            )
            if answer == "no":
                return
        # Do this if it's important to stop engines before closing the Python process
        #  self.stop_all_running_engines()
        #  self._exit_when_all_stopped()
        # Do this if it's OK to just quit
        self.icon.stop()
        self.after(0, self.destroy)

    def _open_aggregator(self):
        url = self.persistent_data["aggregator_hostname"]
        if self.persistent_data["aggregator_secure"]:
            url = "https://"+url
        else:
            url = "http://"+url
        if self.persistent_data["aggregator_secure"] and self.persistent_data["aggregator_port"] == 443:
            pass
        elif self.persistent_data["aggregator_port"] == 80:
            pass
        else:
            url = url+':'+self.persistent_data["aggregator_port"]
        webbrowser.open(url)

    def _close_window(self):
        if self.ask_before_exit():
            self.withdraw()
            # Avoid showing the notification many times in a short timespan
            if (time.time()-self._notification_message_time) > 300:
                self.icon.notify("Open Pectus Engine Manager is still " +
                                 "running in the background.")
            self._notification_message_time = time.time()
        else:
            self._exit(None, None)

    def _load_engines(self, *args, **kwargs):
        uod_filenames = filedialog.askopenfilenames(
            title="Load Open Pectus UOD(s)",
            filetypes=(("Open Pectus UOD", "*.py"),)
        )
        for uod_filename in uod_filenames:
            if self.load_uod_file(uod_filename):
                self.persistent_data["uods"] = self.persistent_data["uods"] + [uod_filename]

    def _about(self):
        from openpectus import __version__ as version
        messagebox.showinfo(
            "About Open Pectus Engine Manager",
            "Run multiple Open Pectus engines in a convenient user interface.\n" +
            f"Open Pectus version: {version}.\n" +
            "Documentation is available at https://docs.openpectus.org/latest/."
        )


def assemble_gui() -> OpenPectusEngineManagerGui:
    # Instantiate objects
    persistent_data = PersistentData()
    gui = OpenPectusEngineManagerGui(persistent_data)
    log_recorder = LogRecorder()
    engine_manager = EngineManager(log_handler=log_recorder, persistent_data=persistent_data)
    # Attach callbacks
    gui.engine_list.on_start_callback.append(
        lambda item: log_recorder.clear_log(item["engine_name"])
    )
    gui.engine_list.on_start_callback.append(engine_manager.start_engine)
    gui.engine_list.on_stop_callback.append(engine_manager.stop_engine)
    gui.engine_list.on_validate_callback.append(
        lambda item: log_recorder.clear_log(item["engine_name"])
    )
    gui.engine_list.on_validate_callback.append(engine_manager.validate_engine)
    gui.add_engine_callback.append(lambda x: log_recorder.engine_names.append(x))
    gui.remove_engine_callback.append(lambda x: log_recorder.engine_names.remove(x))
    log_recorder.emit_callbacks.append(gui.engine_list.set_tag_for_engine_name)
    log_recorder.emit_callbacks.append(
        gui.engine_output.insert_log_record_for_engine
    )
    # Override methods
    engine_manager.set_status_for_item = gui.engine_list.set_status_for_item
    gui.ask_before_exit = lambda: len(engine_manager.get_running_engines()) > 0
    gui.stop_all_running_engines = engine_manager.stop_all_running_engines
    gui.engine_list.load_engine = gui._load_engines
    # Populate GUI with persistent data
    for uod_filename in persistent_data["uods"]:
        gui.load_uod_file(uod_filename)
    return gui


def main():
    gui = assemble_gui()
    # Start Tk event loop
    gui.mainloop()


if __name__ == "__main__":
    multiprocess.spawn.freeze_support()
    main()
