import asyncio


class LocationPrinter:

    def formatter(self, data):
        formatted = []
        for d in data:
            name, link, price, location, date, contact = d
            formatted.append((name, link, f'**{price}**',
                              location, f'({date})', contact))
        return formatted

    def generator(self, data):
        formatted = self.formatter(data)
        if formatted:
            for i in range(0, len(formatted), 5):
                sanitizer = {ord('.'): '\\.', ord('+'): '\\+', ord('-'): '\\-',
                             ord(')'): '\\)', ord('('): '\\(', ord('!'): '\\!', ord('|'): '\\|'}
                yield "\n\n".join([f'[{offer[0].translate(sanitizer)}]({offer[1].translate(sanitizer)})\n{offer[2]}\n{offer[3].translate(sanitizer)}\n{offer[4].translate(sanitizer)}\n{offer[5].translate(sanitizer)}' for offer in formatted[i:i+5]])

    def __init__(self, queue, bot, offer_list):
        self.offer_generator = self.generator(offer_list)
        self.queue = queue
        self.bot = bot
        self.is_working = False

    async def start(self, message):
        if not self.is_working:
            chat_info = await self.bot.get_chat(message.chat.id)
            self.delay = chat_info.slow_mode_delay if chat_info.slow_mode_delay else 3
            self.message = message
            self.is_working = True
            await self.__add_to_queue()
        else:
            raise Exception("printer already running")

    def check_then_stop(self, message):
        if not self.is_working:
            return
        if message.chat.id == self.message.chat.id:
            if self.message.message_thread_id == message.message_thread_id:
                self.is_working = False
        print(f"stop: {self.message.text} {not self.is_working}")
        return not self.is_working

    async def __reply(self, message):
        if not self.is_working:
            return

        messages = next(self.offer_generator, None)
        if messages:
            print(len(messages), messages)
        if message.message_thread_id:
            await self.bot.send_message(message.chat.id, messages,
                                        message_thread_id=message.message_thread_id)
        else:
            await self.bot.send_message(message.chat.id, messages)
        await asyncio.sleep(self.delay)
        await self.__add_to_queue()

    async def __add_to_queue(self):
        await self.queue.put((self.message, self.__reply))

    def stop_if_id(self, id):
        self.is_working = False
