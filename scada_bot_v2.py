#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SCADA Bot v2 — Entry point wrapper.
Semua kode telah di-split ke modul scada_bot/.
File ini tetap ada sebagai backward-compat entry point.
"""
from scada_bot.main import main

if __name__ == "__main__":
    main()
