#!/bin/bash
# Ollama container ayağa kalktıktan sonra modeli çeker
MODEL=${OLLAMA_MODEL:-llama3.2:3b}
echo "Model çekiliyor: $MODEL"
docker exec itsm-agent-ollama-1 ollama pull "$MODEL"
echo "Tamamlandı."
