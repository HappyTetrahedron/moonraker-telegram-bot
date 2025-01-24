from telegram import InputMediaPhoto
from telegram.constants import ChatAction, ParseMode
from telegram.helpers import escape_markdown


class TelegramMessageRepr:
    def __init__(
        self,
        text="",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=None,
        silent=False,
        suppress_escaping=False,
    ):
        if parse_mode == ParseMode.MARKDOWN_V2 and not suppress_escaping:
            self._text = escape_markdown(text, version=2)
        else:
            self._text = text
        self._parse_mode = parse_mode
        self._reply_markup = reply_markup
        self._silent = silent

    def is_silent(self):
        return self._silent

    async def send_as_reply(self, other_message, photo=None):
        if photo:
            await other_message.get_bot().send_chat_action(other_message.chat_id, action=ChatAction.UPLOAD_PHOTO)
            await other_message.reply_photo(
                photo=photo,
                caption=self._text,
                parse_mode=self._parse_mode,
                disable_notification=self._silent,
                reply_markup=self._reply_markup,
            )
        else:
            await other_message.get_bot().send_chat_action(other_message.chat_id, action=ChatAction.TYPING)
            await other_message.reply_text(
                self._text,
                parse_mode=self._parse_mode,
                disable_notification=self._silent,
                quote=True,
                reply_markup=self._reply_markup,
            )

    async def send(self, bot, chat_id, photo=None, message_thread_id=None):
        if photo:
            return await bot.send_photo(
                chat_id,
                photo=photo,
                caption=self._text,
                parse_mode=self._parse_mode,
                reply_markup=self._reply_markup,
                disable_notification=self._silent,
                message_thread_id=message_thread_id,
            )
        else:
            return await bot.send_message(
                chat_id,
                text=self._text,
                parse_mode=self._parse_mode,
                reply_markup=self._reply_markup,
                disable_notification=self._silent,
                message_thread_id=message_thread_id,
            )

    async def update_existing(self, other_message, photo=None):
        if photo:
            # Fixme: check if media in message!
            await other_message.edit_media(media=InputMediaPhoto(photo))
        if other_message.caption:
            await other_message.edit_caption(
                caption=self._text,
                parse_mode=self._parse_mode,
                reply_markup=self._reply_markup,
            )
        else:
            await other_message.edit_text(
                text=self._text,
                parse_mode=self._parse_mode,
                reply_markup=self._reply_markup,
            )
