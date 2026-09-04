"""Ground-Air map loading and initial-pose relocalization adapter."""

import threading


class InitialPoseAdapter:
    def __init__(self, map_id, load_map, relocalize, timeout, logger):
        self.map_id = str(map_id)
        self.load_map = load_map
        self.relocalize = relocalize
        self.timeout = float(timeout)
        self.logger = logger
        self._lock = threading.Lock()

    def load(self):
        response = self.load_map(self.map_id, "")
        if not response.success:
            raise RuntimeError("map load failed: {}".format(response.message))
        self.logger("map loaded: {}".format(response.message))

    def handle_initial_pose(self, message):
        if not self._lock.acquire(False):
            self.logger("initial pose ignored while relocalization is running")
            return None
        try:
            response = self.relocalize(True, message, self.timeout)
            if not response.success:
                raise RuntimeError("relocalization rejected: {}".format(
                    response.message))
            self.logger(
                "relocalization accepted fitness={} rmse={}".format(
                    response.fitness, response.rmse
                )
            )
            return response
        finally:
            self._lock.release()
