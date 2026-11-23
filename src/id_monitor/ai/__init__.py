"""Mapeamento e auto-reparo de sites de concorrentes usando a API do Claude.

A IA so e chamada para (1) mapear um site novo e (2) reparar um site cuja coleta quebrou. O
resultado do mapeamento fica salvo em config/competitors.yaml; a coleta normal nao gasta tokens.
"""
