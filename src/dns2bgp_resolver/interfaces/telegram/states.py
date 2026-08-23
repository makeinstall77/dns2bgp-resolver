from aiogram.fsm.state import State, StatesGroup


class AddDomain(StatesGroup):
    waiting_name = State()


class RemoveDomain(StatesGroup):
    waiting_name = State()


class SearchAuto(StatesGroup):
    waiting_query = State()


class AddFilter(StatesGroup):
    waiting_keyword = State()


class RemoveFilter(StatesGroup):
    waiting_keyword = State()


class AddListUrl(StatesGroup):
    waiting_name = State()
    waiting_url = State()


class AddListFile(StatesGroup):
    waiting_name = State()


class SetGlobalInterval(StatesGroup):
    waiting_seconds = State()


class SetListInterval(StatesGroup):
    waiting_seconds = State()
