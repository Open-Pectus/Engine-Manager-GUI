import unittest
import os
import time
from logging.handlers import BufferingHandler
from typing import List

import openpectus_engine_manager_gui
from openpectus.engine.configuration import demo_uod

gui = openpectus_engine_manager_gui.assemble_gui()
gui.icon.stop()


class TestPersistentData(unittest.TestCase):
    def test_persistent_data_exists(self):
        self.assertTrue(os.path.isfile(gui.persistent_data.filename))

    def test_read_write_persistent_data(self):
        data = gui.persistent_data
        keys = [
            "aggregator_hostname",
            "aggregator_port",
            "aggregator_secure",
            "uods",
        ]
        for key in keys:
            data[key] = data[key]


def engine_manager_factory(uods: List[str]) -> openpectus_engine_manager_gui.EngineManager:
    # Set up Engine Manager object with Null logging
    # handler and dict in memory instead of persistent
    # data. Override set_status_for_item with no-op.
    em = openpectus_engine_manager_gui.EngineManager(
        BufferingHandler(1000),  # Capacity of 1000 records
        dict(
            aggregator_hostname="github.openpectus.org",
            aggregator_port=443,
            aggregator_secure=True,
            uods=[demo_uod.__file__],
        ),
    )
    return em


class TestEngineManager(unittest.TestCase):
    def test_start_stop_engine(self):
        # Test using demo UOD shipped with Open Pectus
        uods = [demo_uod.__file__]
        engine_item = dict(
                engine_name="Unittest",
                filename=demo_uod.__file__,
        )
        # Create list to hold engine status messages
        status_list = list()
        # Create Engine Manager
        em = engine_manager_factory(uods)
        em.set_status_for_item = lambda status, item: status_list.append(status)
        # Start engine and wait until fully started
        em.start_engine(engine_item)
        t0 = time.time()
        assert isinstance(em.log_handler, BufferingHandler)
        while True:
            buffer = em.log_handler.buffer
            if len(buffer) and buffer[-1].msg == "Started steady-state sending loop":
                break
            if time.time() - t0 >= 10:
                raise Exception("Engine manager was unable to start engine.")
            time.sleep(1)
        # Check engine is running properly
        self.assertIn(engine_item["engine_name"], em.loops)
        self.assertIn(engine_item["engine_name"], em.threads)
        loop = em.loops[engine_item["engine_name"]]
        thread = em.threads[engine_item["engine_name"]]
        self.assertTrue(loop.is_running())
        self.assertTrue(thread.is_alive())
        # Stop engine
        em.stop_engine(engine_item)
        t0 = time.time()
        while loop.is_running() or thread.is_alive():
            if time.time() - t0 >= 10:
                raise Exception("Engine manager was unable to stop engine.")
            time.sleep(1)

    def test_validate_engine(self):
        # Test using demo UOD shipped with Open Pectus
        uods = [demo_uod.__file__]
        engine_item = dict(
                engine_name="Unittest",
                filename=demo_uod.__file__,
        )
        # Create list to hold engine status messages
        status_list = list()
        # Create Engine Manager
        em = engine_manager_factory(uods)
        em.set_status_for_item = lambda status, item: status_list.append(status)
        # Validate engine and wait until complete
        em.validate_engine(engine_item)
        self.assertIn(engine_item["engine_name"], em.threads)
        thread = em.threads[engine_item["engine_name"]]
        t0 = time.time()
        while thread.is_alive():
            if time.time() - t0 >= 30:
                raise Exception("Engine manager was unable to validate engine.")
            time.sleep(1)
        self.assertTrue(len(status_list))
        self.assertEqual(status_list[0], "Not running")

    def test_validate_multiple_engines_simultaneously(self):
        # Test using demo UOD shipped with Open Pectus
        uods = [demo_uod.__file__]
        engine_item = dict(
                engine_name="Unittest",
                filename=demo_uod.__file__,
        )
        threads = []
        for i in range(5):
            # Create Engine Manager
            em = engine_manager_factory(uods)
            em.set_status_for_item = lambda status, item: None
            # Validate engine and wait until complete
            em.validate_engine(engine_item)
            threads.append(em.threads[engine_item["engine_name"]])
        t0 = time.time()
        while any([thread.is_alive() for thread in threads]):
            if time.time() - t0 >= 60:
                raise Exception("Engine manager was unable to validate engine.")
            time.sleep(1)


if __name__ == "__main__":
    unittest.main()
