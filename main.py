import os
import logging
import json
import httpx
import asyncio
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# LangChain Imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_community.chat_message_histories import RedisChatMessageHistory
from langchain.memory import ConversationBufferMemory
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder, SystemMessagePromptTemplate, HumanMessagePromptTemplate
from langchain.chains import LLMChain

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- Variáveis de Ambiente ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN")
UPSTASH_REDIS_URL = os.getenv("UPSTASH_REDIS_URL")
CALENDLY_API_TOKEN = os.getenv("CALENDLY_API_TOKEN")
CALENDLY_EVENT_TYPE_UUID = os.getenv("CALENDLY_EVENT_TYPE_UUID")

# --- PROMPT MESTRE REFORÇADO ---
PROMPT_MESTRE = """
Você é Sofia, a IA de qualificação de negócios da Cognox.ai. Sua única missão é identificar dores de negócio de leads que possam ser resolvidas com IA customizada e agendar uma reunião de 15 minutos. Você é estratégica, focada e especialista em soluções Cognox.

REGRAS INVIOLÁVEIS (NUNCA QUEBRE):
1.  **NÃO DÊ TUTORIAIS:** Sua função é diagnosticar, não ensinar. Evite respostas longas e genéricas.
2.  **NÃO SEJA SUPORTE TÉCNICO:** Se um usuário mencionar uma ferramenta (ex: ClickUp), ignore a ferramenta. Foque no PROCESSO e no PROBLEMA de negócio (ex: "entrada manual de dados", "perda de tempo").
3.  **SEMPRE CONECTE À COGNOX:** Qualquer problema de processo deve ser imediatamente conectado a uma solução da Cognox. Ex: "Entendi, automatizar esse tipo de tarefa é exatamente o que fazemos na Cognox.ai com IAs personalizadas."
4.  **SEU OBJETIVO É A REUNIÃO:** Após identificar e conectar uma dor de negócio válida, seu próximo passo é SEMPRE sugerir a reunião.
5.  **USE O PLACEHOLDER DO CALENDLY:** Quando o usuário concordar em agendar, sua resposta final deve conter o texto exato `[LINK_CALENDLY]`.
"""

# --- INICIALIZAÇÃO LANGCHAIN ---
try:
    llm = ChatGoogleGenerativeAI(model="gemini-1.5-flash", temperature=0.7, google_api_key=GEMINI_API_KEY)
    prompt = ChatPromptTemplate(messages=[
        SystemMessagePromptTemplate.from_template(PROMPT_MESTRE),
        MessagesPlaceholder(variable_name="chat_history"),
        HumanMessagePromptTemplate.from_template("{input}")
    ])
    logger.info("LangChain e Gemini inicializados com sucesso.")
except Exception as e:
    logger.error(f"Erro ao inicializar LangChain/Gemini: {e}")
    raise

app = FastAPI(title="Sofia - Assistente IA Cognox.ai", version="6.0-prod")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# --- FUNÇÕES AUXILIARES ---
async def send_whatsapp_message(to_number: str, message: str):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_number, "type": "text", "text": {"body": message}}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=data)
            response.raise_for_status()
            logger.info(f"Mensagem enviada para {to_number}")
        except httpx.HTTPStatusError as e:
            logger.error(f"ERRO AO ENVIAR MENSAGEM WHATSAPP: {e.response.text}")
        except Exception as e:
            logger.error(f"ERRO WHATSAPP: {e}")

async def process_message_task(user_id: str, user_message: str):
    logger.info(f"Processando mensagem para {user_id}: '{user_message}'")
    message_history = RedisChatMessageHistory(url=UPSTASH_REDIS_URL, session_id=user_id)
    memory = ConversationBufferMemory(memory_key="chat_history", chat_memory=message_history, return_messages=True)
    conversation_chain = LLMChain(llm=llm, prompt=prompt, memory=memory, verbose=True)
    try:
        response = await conversation_chain.ainvoke({"input": user_message})
        final_response_text = response['text']
        if "[LINK_CALENDLY]" in final_response_text:
            calendly_link = f"https://calendly.com/cognoxai/{CALENDLY_EVENT_TYPE_UUID or '15min'}"
            final_response_text = final_response_text.replace("[LINK_CALENDLY]", calendly_link)
        await send_whatsapp_message(user_id, final_response_text)
    except Exception as e:
        logger.error(f"Erro ao processar com LangChain: {e}")
        await send_whatsapp_message(user_id, "Desculpe, tive um problema técnico. Por favor, tente novamente.")

# --- ROTAS DA API ---
@app.get("/webhook")
async def verify_webhook(request: Request):
    if "hub.mode" in request.query_params and "hub.verify_token" in request.query_params:
        mode, token, challenge = request.query_params.get("hub.mode"), request.query_params.get("hub.verify_token"), request.query_params.get("hub.challenge")
        if mode == "subscribe" and token == META_VERIFY_TOKEN:
            logger.info("WEBHOOK VERIFICADO COM SUCESSO!")
            return HTMLResponse(content=challenge, status_code=200)
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    logger.info(f"Webhook recebido: {json.dumps(body, indent=2)}")
    try:
        message = body["entry"][0]["changes"][0]["value"]["messages"][0]
        if message["type"] == "text":
            user_id = message["from"]
            user_message = message["text"]["body"]
            background_tasks.add_task(process_message_task, user_id, user_message)
    except (KeyError, IndexError):
        pass
    return HTMLResponse(content="OK", status_code=200)

@app.get("/")
async def root():
    return {"message": "Sofia - Assistente IA Cognox.ai"}
