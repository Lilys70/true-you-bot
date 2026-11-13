"""Telegram-бот для диагностики уровня самооценки.

Учебный проект Zerocoder, шаги 1-4: бот задаёт 15 вопросов из
данные/questions.py, считает баллы с инверсией обратных вопросов,
показывает результат, а после него предлагает перейти в канал
или записаться на консультацию (заявка сохраняется в хранилище/заявки.json).
Схема сценария для проверки и портфолио — схема_бота.json.

Запуск: python bot.py (токен берётся из файла .env, в коде его нет).
"""

import asyncio
import logging
import os
import sys
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

from данные.questions import (
    ANSWER_OPTIONS,
    QUESTIONS,
    apply_answer,
    calculate_result,
)
from хранилище.заявки import save_application

# --- Тексты сообщений -------------------------------------------------------
# Пока храним в коде: тексты вопросов и результатов уже вынесены
# в данные/questions.py, служебные сообщения останутся здесь до дня 13.

WELCOME_TEXT = (
    "Здравствуйте! Это бот для диагностики уровня самооценки.\n\n"
    "За 15 коротких вопросов он поможет понять, как вы оцениваете себя "
    "и насколько уверенно чувствуете себя в разных ситуациях.\n\n"
    "Тест занимает 3-5 минут. Результат покажу сразу после последнего ответа."
)

INSTRUCTION_TEXT = (
    "Тест состоит из 15 вопросов. Отметьте, как часто вам подходит "
    "утверждение:\n\n"
    "Никогда → Редко → Иногда → Часто → Очень часто\n\n"
    "Отвечайте честно: правильных и неправильных ответов здесь нет. "
    "Чем точнее ответы, тем полезнее результат."
)

# Ссылка на канал: заглушка, заменить на реальный адрес канала владельца бота.
CHANNEL_URL = "https://t.me/your_channel"

BOOKING_NAME_TEXT = (
    "Как вас зовут? Напишите имя — оно попадёт в заявку на консультацию."
)

BOOKING_TIME_TEXT = (
    "Спасибо, {name}! Когда вам удобно созвониться и как удобнее общаться?\n\n"
    "Напишите в свободной форме, например: «завтра после 18:00, Telegram» "
    "или «в субботу днём, WhatsApp»."
)

BOOKING_CONTACT_TEXT = (
    "Остался последний шаг: оставьте контакт для связи — телефон "
    "или username в Telegram/WhatsApp.\n\n"
    "Контакт нужен только для ответа по этой заявке."
)

BOOKING_DONE_TEXT = "Спасибо! Мы свяжемся с вами в ближайшее время."

BOOKING_SAVE_ERROR_TEXT = (
    "Не получилось сохранить заявку. Попробуйте, пожалуйста, ещё раз "
    "через минуту."
)


class TestStates(StatesGroup):
    """Состояния прохождения теста."""

    answering = State()  # пользователь отвечает на вопросы


class BookingStates(StatesGroup):
    """Состояния записи на консультацию."""

    waiting_name = State()  # ждём имя
    waiting_time_format = State()  # ждём удобное время и формат связи
    waiting_contact = State()  # ждём телефон или username


# --- Клавиатуры -------------------------------------------------------------
def start_keyboard() -> InlineKeyboardMarkup:
    """Кнопка под приветствием: вход в тест."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Начать тест",
                    callback_data="start_test",
                )
            ]
        ]
    )


def go_keyboard() -> InlineKeyboardMarkup:
    """Кнопка под инструкцией: переход к вопросам."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Поехали", callback_data="test_go")]
        ]
    )


def answer_keyboard() -> InlineKeyboardMarkup:
    """Пять кнопок с вариантами ответа: ans_0 ... ans_4."""
    buttons = [
        [InlineKeyboardButton(text=label, callback_data=f"ans_{score}")]
        for label, score in ANSWER_OPTIONS
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def channel_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «в канал»: URL-кнопка, состояние бота не меняет."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти в Telegram-канал",
                    url=CHANNEL_URL,
                )
            ]
        ]
    )


def result_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под результатом: канал, запись на консультацию, рестарт."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Перейти в Telegram-канал",
                    url=CHANNEL_URL,
                )
            ],
            [
                InlineKeyboardButton(
                    text="Записаться на консультацию",
                    callback_data="book_consult",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Пройти ещё раз",
                    callback_data="restart",
                )
            ],
        ]
    )


# --- Вспомогательные функции -------------------------------------------------
async def safe_delete(message: Message) -> None:
    """Удаляет сообщение и не падает, если оно уже удалено."""
    try:
        await message.delete()
    except TelegramBadRequest:
        # Сообщение старше 48 часов или уже удалено — это не ошибка.
        pass


def format_result(result: dict) -> str:
    """Собирает текст результата из ответа calculate_result()."""
    return (
        f"<b>{escape(result['title'])}</b>\n\n"
        f"Ваш результат: <b>{result['total']}</b> "
        f"из {result['max_total']} баллов.\n\n"
        f"{escape(result['description'])}\n\n"
        f"<b>Совет.</b> {escape(result['advice'])}\n\n"
        "Чтобы пройти тест заново, отправьте /start."
    )


async def send_question(
    bot: Bot,
    chat_id: int,
    state: FSMContext,
    index: int,
) -> None:
    """Отправляет вопрос с клавиатурой и запоминает id сообщения.

    id нужен, чтобы отличить нажатие на кнопку актуального вопроса
    от нажатия на кнопку из старого сообщения.
    """
    question = QUESTIONS[index]
    text = (
        f"Вопрос {index + 1} из {len(QUESTIONS)}\n\n"
        f"{question['text']}\n\n"
        "Как часто это про вас?"
    )
    message = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=answer_keyboard(),
    )
    await state.update_data(message_id=message.message_id)


# --- Обработчики ------------------------------------------------------------
async def cmd_start(message: Message, state: FSMContext) -> None:
    """Команда /start: сбрасывает прерванный тест и показывает приветствие."""
    # Если пользователь бросил тест на середине, начинаем с чистого листа.
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=start_keyboard())


async def on_start_test(callback: CallbackQuery) -> None:
    """Нажатие «Начать тест»: показываем инструкцию и кнопку «Поехали»."""
    # callback.message может быть None, если сообщение удалено, — проверяем.
    if callback.message is not None:
        await callback.message.answer(
            INSTRUCTION_TEXT,
            reply_markup=go_keyboard(),
        )
    # Убираем «часики» на кнопке, иначе Telegram крутит их 30 секунд.
    await callback.answer()


async def on_test_go(callback: CallbackQuery, state: FSMContext) -> None:
    """«Поехали»: включаем состояние ответов и показываем первый вопрос."""
    await callback.answer()

    if callback.message is None:
        return

    # Обнуляем данные FSM: индекс вопроса и список зачётных баллов.
    await state.set_state(TestStates.answering)
    await state.update_data(index=0, scores=[], message_id=None)

    # Инструкцию убираем, чтобы в чате остался только актуальный вопрос.
    await safe_delete(callback.message)
    await send_question(callback.bot, callback.message.chat.id, state, 0)


async def on_answer(callback: CallbackQuery, state: FSMContext) -> None:
    """Приём ответа на вопрос: балл, следующий вопрос или результат."""
    data = await state.get_data()
    index = data.get("index")
    scores = list(data.get("scores", []))
    active_message_id = data.get("message_id")

    if callback.message is None:
        await callback.answer(
            "Сообщение устарело, отправьте /start",
            show_alert=True,
        )
        return

    # Состояние могло остаться без данных после перезапуска бота.
    if index is None or index >= len(QUESTIONS):
        await state.clear()
        await callback.answer(
            "Тест уже завершён, отправьте /start",
            show_alert=True,
        )
        return

    # Кнопка из прошлого сообщения: балл не засчитываем, иначе ответ
    # на старый вопрос попадёт в счёт дважды.
    if callback.message.message_id != active_message_id:
        await callback.answer("Этот вопрос уже неактуален")
        return

    # callback_data вида ans_0 ... ans_4: цифра после подчёркивания.
    try:
        answer = int(callback.data.split("_", maxsplit=1)[1])
    except (IndexError, ValueError):
        await callback.answer("Не удалось прочитать ответ, нажмите ещё раз")
        return

    question = QUESTIONS[index]

    # Инверсия обратных вопросов: «очень часто» на негативное утверждение
    # снижает балл, а не повышает его.
    scores.append(apply_answer(question, answer))

    await callback.answer()
    # Убираем вопрос из чата, чтобы не копились старые кнопки.
    await safe_delete(callback.message)

    next_index = index + 1
    if next_index < len(QUESTIONS):
        await state.update_data(index=next_index, scores=scores)
        await send_question(
            callback.bot,
            callback.message.chat.id,
            state,
            next_index,
        )
        return

    # Вопросы закончились: считаем результат и сбрасываем состояние.
    result = calculate_result(scores)
    await state.clear()
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text=format_result(result),
        reply_markup=result_keyboard(),
    )


async def on_book_consult(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Записаться на консультацию»: начинаем собирать данные."""
    await callback.answer()

    if callback.message is None:
        return

    # Поля заявки заполняем по шагам, старые значения обнуляем.
    await state.set_state(BookingStates.waiting_name)
    await state.update_data(name=None, time_format=None, contact=None)
    await callback.message.answer(BOOKING_NAME_TEXT)


async def on_booking_name(message: Message, state: FSMContext) -> None:
    """Состояние waiting_name: запоминаем имя и спрашиваем время."""
    name = (message.text or "").strip()
    if not 2 <= len(name) <= 64:
        await message.answer(
            "Имя должно быть от 2 до 64 символов. "
            "Напишите, пожалуйста, ещё раз."
        )
        return

    await state.update_data(name=name)
    await state.set_state(BookingStates.waiting_time_format)
    # Имя подставляем через escape: в тексте включён HTML, и символ «<»
    # в имени сломал бы разметку — сообщение просто не отправилось бы.
    await message.answer(BOOKING_TIME_TEXT.format(name=escape(name)))


async def on_booking_time_format(message: Message, state: FSMContext) -> None:
    """Состояние waiting_time_format: запоминаем время и формат связи."""
    time_format = (message.text or "").strip()
    if not 2 <= len(time_format) <= 200:
        await message.answer(
            "Напишите, пожалуйста, удобное время и формат связи текстом."
        )
        return

    await state.update_data(time_format=time_format)
    await state.set_state(BookingStates.waiting_contact)
    await message.answer(BOOKING_CONTACT_TEXT)


async def on_booking_contact(message: Message, state: FSMContext) -> None:
    """Состояние waiting_contact: сохраняем заявку и завершаем запись."""
    contact = (message.text or "").strip()
    if not 3 <= len(contact) <= 100:
        await message.answer(
            "Проверьте, пожалуйста, контакт: нужен телефон или username "
            "длиной от 3 символов."
        )
        return

    data = await state.get_data()
    name = data.get("name") or "не указано"
    time_format = data.get("time_format") or "не указано"
    user_id = message.from_user.id if message.from_user else 0

    try:
        save_application(user_id, name, time_format, contact)
    except (OSError, ValueError) as error:
        # Заявка не сохранилась: состояние не сбрасываем, чтобы человек
        # просто повторил отправку контакта, а не проходил всё заново.
        logging.exception("Не удалось сохранить заявку: %s", error)
        await message.answer(BOOKING_SAVE_ERROR_TEXT)
        return

    await state.clear()
    logging.info("Заявка сохранена, user_id=%s", user_id)
    await message.answer(BOOKING_DONE_TEXT, reply_markup=channel_keyboard())


async def on_restart(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Пройти ещё раз»: сбрасываем состояние и показываем старт."""
    await callback.answer()
    await state.clear()

    if callback.message is not None:
        await callback.message.answer(
            WELCOME_TEXT,
            reply_markup=start_keyboard(),
        )


async def cmd_reset(message: Message, state: FSMContext) -> None:
    """Команда /reset: сбрасывает тест или заявку и начинает заново."""
    await state.clear()
    await message.answer(
        "Сбросила ответы теста и незаконченную заявку.\n\n" + WELCOME_TEXT,
        reply_markup=start_keyboard(),
    )


def register_handlers(dp: Dispatcher) -> None:
    """Подключает обработчики к диспетчеру.

    Вынесено в отдельную функцию, чтобы тест мог собрать такой же диспетчер
    и проверить, что ни один обработчик не забыт.
    """
    # Команды: старт и сброс.
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_reset, Command("reset"))
    dp.callback_query.register(on_start_test, F.data == "start_test")
    dp.callback_query.register(on_test_go, F.data == "test_go")
    # Ответы принимаем только в состоянии прохождения теста.
    dp.callback_query.register(
        on_answer,
        TestStates.answering,
        F.data.startswith("ans_"),
    )
    # Кнопки под результатом: запись на консультацию и повторный тест.
    dp.callback_query.register(on_book_consult, F.data == "book_consult")
    dp.callback_query.register(on_restart, F.data == "restart")
    # Текстовые ответы принимаем только в своём состоянии записи.
    dp.message.register(on_booking_name, BookingStates.waiting_name)
    dp.message.register(on_booking_time_format, BookingStates.waiting_time_format)
    dp.message.register(on_booking_contact, BookingStates.waiting_contact)


# --- Запуск -----------------------------------------------------------------
def read_token() -> str:
    """Читает токен из окружения.

    Токен хранится только в .env — в коде и в репозитории его быть не должно.
    """
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token or token == "ваш_токен_здесь":
        raise RuntimeError(
            "Не найден BOT_TOKEN. Скопируйте .env.example в .env "
            "и вставьте токен, который выдал @BotFather."
        )
    return token


async def main() -> None:
    """Настраивает бота и запускает получение сообщений (long polling)."""
    load_dotenv()  # подхватываем переменные из .env рядом с bot.py

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    bot = Bot(
        token=read_token(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Данные теста храним в памяти процесса: при перезапуске бота
    # незавершённые тесты сбрасываются, для учебного проекта этого хватает.
    dp = Dispatcher(storage=MemoryStorage())

    # Регистрируем обработчики: команды, тест, результат, запись на консультацию.
    register_handlers(dp)

    # Снимаем старый вебхук, чтобы polling не конфликтовал с ним.
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as error:
        # Понятное сообщение вместо длинного трейсбека, если не заполнен .env.
        print(f"Бот не запущен: {error}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("Бот остановлен вручную.")
