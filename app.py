import os
import json
from pyclbr import Class
from urllib import response
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import httpx
import threading

load_dotenv()

# Lock para evitar corrupção de dados no acesso concorrente ao arquivo JSON
history_lock = threading.Lock()

app = FastAPI(title="Homeflix API", description="API for Homeflix")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Em produção, você pode trocar "*" pelo domínio do seu front (ex: "http://localhost:3000")
    allow_credentials=True,
    allow_methods=["*"],  # Permite GET, POST, PUT, DELETE, etc.
    allow_headers=["*"],
)

TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TMDB_BASE_URL = "https://api.themoviedb.org/3"
EXTERNAL_API_URL = os.getenv("EXTERNAL_API_URL")
SUPERFLIX_BASE_URL = os.getenv("SUPERFLIX_BASE_URL")

HEADERS = {"User-Agent": "Mozilla/5.0"}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "data/history.json")

from typing import Optional, List

class HistoryItem(BaseModel):
    tmdb_id: str
    titulo: str
    tipo: str
    capa: str
    temporada: Optional[int] = None
    episodio: Optional[int] = None
    position: Optional[float] = 0.0  # Tempo atual em segundos
    duration: Optional[float] = 1.0  # Duração total em segundos (default 1 para evitar divisão por zero)
    updatedAt: Optional[float] = None # Timestamp para ordenação

@app.post("/api/history")
def add_history(item: HistoryItem):
    """Salva um novo item no histórico."""
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with history_lock:
            if not os.path.exists(HISTORY_FILE):
                history = []
            else:
                with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                    try:
                        history = json.load(f)
                    except json.JSONDecodeError:
                        history = []
            
            # Remove se já existir (mesmo tmdb_id e tipo) para manter apenas o último assistido (independente de temporada/episódio)
            history = [h for h in history if not (h["tmdb_id"] == item.tmdb_id and h.get("tipo") == item.tipo)]
            
            history.append(item.model_dump())
            
            # Limita o tamanho do histórico para evitar problemas de performance
            history = history[-100:]
            
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=4)
                
        return {"status": "success"}
    except Exception as e:
        print(f"Erro detalhado ao salvar histórico: {e}") # Log para debug
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history")
def get_history():
    """Retorna o histórico de visualização."""
    if not os.path.exists(HISTORY_FILE):
        return []
    with history_lock:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

@app.delete("/api/history/{tipo}/{tmdb_id}")
def delete_history(tipo: str, tmdb_id: str):
    """Remove um item do histórico."""
    if not os.path.exists(HISTORY_FILE):
        return {"status": "success"}
    try:
        with history_lock:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                try:
                    history = json.load(f)
                except json.JSONDecodeError:
                    history = []
            
            initial_len = len(history)
            # Converte para string para comparação segura, caso tenha salvo como int
            history = [h for h in history if not (str(h.get("tmdb_id")) == tmdb_id and h.get("tipo") == tipo)]
            
            if len(history) != initial_len:
                with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                    json.dump(history, f, indent=4)
                    
        return {"status": "success"}
    except Exception as e:
        print(f"Erro ao deletar histórico: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
async def read_index():
    index_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"error": "index.html not found"}

@app.get("/details.html")
async def read_details_page():
    # Certifique-se de que o arquivo details.html está na pasta templates (ou na raiz do projeto)
    details_path = os.path.join(BASE_DIR, "templates/details.html")
    
    if os.path.exists(details_path):
        return FileResponse(details_path)
    
    # Se o HTML não estiver na pasta 'templates', tente buscar na raiz:
    root_details_path = os.path.join(BASE_DIR, "details.html")
    if os.path.exists(root_details_path):
        return FileResponse(root_details_path)

    raise HTTPException(status_code=404, detail="Arquivo details.html não encontrado no servidor.")

# -------------------------------------------------------------------
# Rotas de Catálogo (Página Inicial)
# -------------------------------------------------------------------

@app.get("/api/catalog/trending")
async def get_trending():
    """Retorna conteúdos em alta no TMDB (Filmes e Séries)."""
    url = f"{TMDB_BASE_URL}/trending/all/week"
    return await _fetch_tmdb_catalog(url)

@app.get("/api/catalog/movies/popular")
async def get_popular_movies():
    """Retorna os filmes mais populares."""
    url = f"{TMDB_BASE_URL}/movie/popular"
    return await _fetch_tmdb_catalog(url, media_type_default="movie")

@app.get("/api/catalog/series/popular")
async def get_popular_series():
    """Retorna as séries mais populares."""
    url = f"{TMDB_BASE_URL}/tv/popular"
    return await _fetch_tmdb_catalog(url, media_type_default="tv")


async def _fetch_tmdb_catalog(url: str, media_type_default: str = None):
    """Função auxiliar para formatar respostas do TMDB."""
    params = {
        "api_key": TMDB_API_KEY,
        "language": "pt-BR",
        "page": 1
    }
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", []):
            media_type = item.get("media_type", media_type_default)
            if media_type not in ["movie", "tv"]:
                continue

            titulo = item.get("title") if media_type == "movie" else item.get("name")
            data_lancamento = item.get("release_date") if media_type == "movie" else item.get("first_air_date")
            poster = item.get("poster_path")

            results.append({
                "tmdb_id": item.get("id"),
                "tipo": "filme" if media_type == "movie" else "serie",
                "titulo": titulo,
                "imagem": f"https://image.tmdb.org/t/p/w500{poster}" if poster else None,
                "ano": data_lancamento[:4] if data_lancamento else None,
                "nota": item.get("vote_average")
            })

        return {"status": "success", "resultados": results}

    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Erro ao carregar catálogo: {e}")



@app.get("/api/search")
async def search_tmdb(q: str = Query(..., min_length=1)):
    """
    Pesquisa filmes e séries no TMDB.
    """
    url = f"{TMDB_BASE_URL}/search/multi"
    params = {
        "api_key": TMDB_API_KEY,
        "query": q,
        "language": "pt-BR",
        "include_adult": "false"
    }
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("results", []):
            media_type = item.get("media_type")
            if media_type not in ["movie", "tv"]:
                continue

            titulo = item.get("title") if media_type == "movie" else item.get("name")
            data_lancamento = item.get("release_date") if media_type == "movie" else item.get("first_air_date")
            poster = item.get("poster_path")

            results.append({
                "tmdb_id": item.get("id"),
                "tipo": "filme" if media_type == "movie" else "serie",
                "titulo": titulo,
                "sinopse": item.get("overview"),
                "imagem": f"https://image.tmdb.org/t/p/w500{poster}" if poster else None,
                "ano": data_lancamento[:4] if data_lancamento else None,
                "nota": item.get("vote_average")
            })

        return {"status": "success", "resultados": results}

    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Erro ao conectar com TMDB: {e}")

@app.get("/api/stream/filme/{tmdb_id}")
async def get_movie_stream(tmdb_id: str, server: str = Query("pomfy")):
    """
    Retorna o link do player embed para um filme via TMDB ID.
    """
    if server == "pomfy":
        player_url = f"{EXTERNAL_API_URL}/filme/{tmdb_id}"
    else:
        player_url = f"{SUPERFLIX_BASE_URL}/filme/{tmdb_id}"
    
    return {
        "status": "success",
        "tipo": "filme",
        "tmdb_id": tmdb_id,
        "server": server,
        "player_url": player_url,
        "iframe": f'<iframe src="{player_url}" width="100%" height="100%" frameborder="0" allowfullscreen></iframe>'
    }

@app.get("/api/stream/serie/{tmdb_id}")
async def get_tv_stream(
    tmdb_id: str, 
    season: int = Query(1, alias="s"), 
    episode: int = Query(1, alias="e"),
    server: str = Query("pomfy")
):
    """
    Retorna o link do player embed para uma série/anime.
    """
    if server == "pomfy":
        player_url = f"{EXTERNAL_API_URL}/serie/{tmdb_id}/{season}/{episode}"
    else:
        player_url = f"{SUPERFLIX_BASE_URL}/serie/{tmdb_id}/{season}/{episode}"
    
    return {
        "status": "success",
        "tipo": "serie",
        "tmdb_id": tmdb_id,
        "season": season,
        "episode": episode,
        "server": server,
        "player_url": player_url,
        "iframe": f'<iframe src="{player_url}" width="100%" height="100%" frameborder="0" allowfullscreen"></iframe>'
    }

@app.get("/api/details/{tipo}/{tmdb_id}")
async def get_details(tipo: str, tmdb_id: str):
    """
    Busca detalhes do filme ou série no TMDB com elenco e dados estruturados.
    """
    # 1. Normaliza o tipo recebido no endpoint para o padrão do TMDB
    tmdb_type = "movie" if tipo in ["filme", "movie"] else "tv"
    
    url = f"{TMDB_BASE_URL}/{tmdb_type}/{tmdb_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "pt-BR",
        # Traz elenco e créditos na mesma requisição
        "append_to_response": "credits" 
    }
    
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            resp = await client.get(url, params=params)
            
            # Trata erro 404 explicitamente se a mídia não existir
            if resp.status_code == 404:
                raise HTTPException(status_code=404, detail="Conteúdo não encontrado no TMDB.")
                
            resp.raise_for_status()
            data = resp.json()

        # 2. Processa o elenco (pega os 5 primeiros atores principais)
        cast_list = data.get("credits", {}).get("cast", [])
        elenco = ", ".join([actor["name"] for actor in cast_list[:5]])

        # 3. Trata imagem nula
        poster_path = data.get("poster_path")
        imagem_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None

        # 4. Formatação base da resposta
        detalhes = {
            "status": "success",
            "tmdb_id": tmdb_id,
            "tipo": "filme" if tmdb_type == "movie" else "serie",
            "titulo": data.get("title") if tmdb_type == "movie" else data.get("name"),
            "sinopse": data.get("overview"),
            "imagem": imagem_url,
            "nota": data.get("vote_average"),
            "generos": ", ".join([g["name"] for g in data.get("genres", [])]),
            "elenco": elenco,
            "player_url": f"/api/stream/{'filme' if tmdb_type == 'movie' else 'serie'}/{tmdb_id}"
        }

        # 5. Adiciona informações extras se for uma Série
        if tmdb_type == "tv":
            detalhes["numero_temporadas"] = data.get("number_of_seasons")
            detalhes["numero_episodios"] = data.get("number_of_episodes")
            detalhes["temporadas"] = [
                {
                    "temporada": s.get("season_number"),
                    "nome": s.get("name"),
                    "episodios_count": s.get("episode_count")
                }
                for s in data.get("seasons", []) if s.get("season_number", 0) > 0
            ]

        return detalhes

    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"Erro na API remota: {e}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Erro de conexão com TMDB: {e}")

@app.get("/api/details/serie/{tmdb_id}/season/{season_number}")
async def get_season_episodes(tmdb_id: str, season_number: int):
    """Busca episódios de uma temporada específica."""
    url = f"{TMDB_BASE_URL}/tv/{tmdb_id}/season/{season_number}"
    params = {"api_key": TMDB_API_KEY, "language": "pt-BR"}
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=10.0) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        return [
            {
                "numero": ep["episode_number"],
                "titulo": ep["name"],
                "sinopse": ep["overview"],
                "imagem": f"https://image.tmdb.org/t/p/w500{ep['still_path']}" if ep.get("still_path") else None
            }
            for ep in data.get("episodes", [])
        ]
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Erro ao buscar episódios: {e}")
if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8000)
    