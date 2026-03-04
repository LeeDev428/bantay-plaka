import json
from channels.generic.websocket import AsyncWebsocketConsumer


class LogConsumer(AsyncWebsocketConsumer):
    GROUP_NAME = 'vehicle_logs'

    async def connect(self):
        await self.channel_layer.group_add(self.GROUP_NAME, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.GROUP_NAME, self.channel_name)

    # receive broadcast from views
    async def log_entry(self, event):
        await self.send(text_data=json.dumps(event['data']))
