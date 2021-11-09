mkPolicy :: POSIXTime -> ScriptContext -> Bool
mkPolicy dl ctx = (to dl) `contains` range
  where
    info :: TxInfo
    info = scriptContextTxInfo ctx

    range :: POSIXTimeRange
    range = txInfoValidRange info
