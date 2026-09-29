from mangum import Mangum
from api import app

# Mangum-адаптер: превращает FastAPI в функцию для serverless
handler = Mangum(app)