from aiogram.fsm.state import State, StatesGroup


class InputFlow(StatesGroup):
    waiting_text = State()
