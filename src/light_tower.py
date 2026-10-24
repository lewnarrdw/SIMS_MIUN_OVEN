class LightTower:

    STATES = {
        "READY": "GREEN",
        "WARMING": "YELLOW",
        "HOLDING": "GREEN",
        "COOLING": "YELLOW",
        "COMPLETE": "GREEN",
        "STOPPED": "RED",
        "ERROR": "RED",
        "OFF": "OFF",
        "IDLE": "OFF",
    }

    def __init__(self):
        self.current_state = None

    @staticmethod
    def _normalise_state(state):
        if state is None:
            return "OFF"
        return str(state).strip().upper()

    def set_state(self, state):
        state_value = self._normalise_state(state)

        if state_value == self.current_state:
            return self.current_state

        self.current_state = state_value
        color = self.STATES.get(state_value, "UNKNOWN")
        print(f"[LIGHT TOWER] {state_value} -> {color}")
        return self.current_state

    def show(self, state):
        return self.set_state(state)

    def close(self):
        return self.set_state("OFF")

    def __call__(self, state):
        return self.set_state(state)