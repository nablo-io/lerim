retry_policy ExtractAgentRetry {
  max_retries 1
  strategy {
    type exponential_backoff
    delay_ms 500
    multiplier 2
    max_delay_ms 8000
  }
}

client<llm> MiniMaxM27 {
  provider "openai-generic"
  retry_policy ExtractAgentRetry
  options {
    base_url "https://api.minimax.io/v1"
    api_key env.MINIMAX_API_KEY
    model "MiniMax-M2.7"
    temperature 0.01
    http {
      connect_timeout_ms 10000
      time_to_first_token_timeout_ms 120000
      idle_timeout_ms 30000
      request_timeout_ms 300000
    }
  }
}

client<llm> OllamaLocal {
  provider "openai-generic"
  retry_policy ExtractAgentRetry
  options {
    base_url "http://127.0.0.1:11434/v1"
    api_key "ollama"
    model "gemma4:e4b"
    temperature 0.0
    http {
      connect_timeout_ms 10000
      time_to_first_token_timeout_ms 120000
      idle_timeout_ms 30000
      request_timeout_ms 300000
    }
  }
}
