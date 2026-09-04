"""Bridge one CCS relocalization launch process to the resident stage manager."""

import threading

from .system_stage_core import BASE, RELOCALIZATION


class RelocalizationStageBridge:
    def __init__(self, service_call, map_id, timeout, logger):
        self.service_call = service_call
        self.map_id = str(map_id)
        self.timeout = float(timeout)
        self.logger = logger
        self._lock = threading.Lock()
        self._released = False

    def acquire(self):
        response = self.service_call(RELOCALIZATION, self.map_id, self.timeout)
        if not response.success or int(response.active_stage) != RELOCALIZATION:
            raise RuntimeError(
                "relocalization stage rejected: {}".format(response.message)
            )
        self.logger("relocalization stage acquired: {}".format(response.message))

    def release(self):
        with self._lock:
            if self._released:
                return
            self._released = True
        try:
            response = self.service_call(BASE, self.map_id, min(self.timeout, 15.0))
            if not response.success:
                self.logger("relocalization stage release rejected: {}".format(
                    response.message))
        except Exception as error:
            self.logger("relocalization stage release failed: {}".format(error))
