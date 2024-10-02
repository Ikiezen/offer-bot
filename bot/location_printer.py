import asyncio


class LocationPrinter:

    async def generator(self, db):
        while True:
            await db.create_new_cursor()
            updates = await db.get_new(self.location)
            # print("updates:", updates)
            await db.invalidate(updates, self.location)
            if not updates:
                yield None
            else:
                sanitizer = {ord('.'): '\\.', ord('+'): '\\+', ord('-'): '\\-', ord('_'): '\\_',
                             ord(')'): '\\)', ord('('): '\\(', ord('!'): '\\!', ord('|'): '\\|'}
                result = ""
                for offer in updates:
                    print(offer)
                    result += f'''[{offer[0].translate(sanitizer)}]({offer[1].translate(sanitizer)})
{str(offer[2])}
{str(offer[3]).translate(sanitizer)}
{str(offer[4]).translate(sanitizer)}
{str(offer[5]).translate(sanitizer)}\n\n'''
                yield result

    def __init__(self, queue, bot, database, location):
        self.offer_generator = self.generator(database)
        self.location = location
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

        messages = await self.offer_generator.__anext__()
        if messages:
            print(len(messages))
            if message.message_thread_id:
                await self.bot.send_message(message.chat.id, messages,
                                            message_thread_id=message.message_thread_id)
            else:
                await self.bot.send_message(message.chat.id, messages)
        await asyncio.sleep(self.delay)
        # print("next")
        await self.__add_to_queue()

    async def __add_to_queue(self):
        await self.queue.put((self.message, self.__reply))

    def stop_if_id(self, id):
        self.is_working = False
