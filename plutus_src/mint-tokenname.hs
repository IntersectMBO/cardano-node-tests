mintTokenName :: TokenName -> ScriptContext -> Bool
mintTokenName tn ctx = traceIfFalse "wrong token name" checkTokenName

  where
    info :: TxInfo
    info = scriptContextTxInfo ctx

    checkTokenName :: Bool
    checkTokenName = valueOf (txInfoMint info) (ownCurrencySymbol ctx) tn > 0
