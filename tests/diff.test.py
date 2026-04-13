import difflib


if __name__ == "__main__":

    new_clean = "What was the indexer APY for 0xe60554D90AF0e84A9C3d1A8643e41e49403945a6 in era 0x51?"
    hist_clean = "What was the indexer APY for 0xe60554D90AF0e84A9C3d1A8643e41e49403945a6 in era 0x51?"
    similarity = difflib.SequenceMatcher(None, new_clean, hist_clean).ratio()
    print("similarity:", similarity)


    new_clean = "What was the indexer APY for 0xe60554D90AF0e84A9C3d1A8643e41e49403945a6 in era 0x51?"
    hist_clean = "What was the total expected reward for indexer 0xF64476a9A06ABC89da3CE502c6E09b22B676C14E in era 0x49?"
    similarity = difflib.SequenceMatcher(None, new_clean, hist_clean).ratio()
    print("similarity:", similarity)

    new_clean = "What was the indexer APY for 0xe60554D90AF0e84A9C3d1A8643e41e49403945a6 in era 0x51?"
    hist_clean = "What was the indexer APY for 0xe60554D90AF0e84A9C3d1A8643e41e49403945a6 in era 0x50?"
    similarity = difflib.SequenceMatcher(None, new_clean, hist_clean).ratio()
    print("similarity:", similarity)
