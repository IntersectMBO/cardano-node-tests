mkPolicy :: PubKeyHash -> ScriptContext -> Bool
mkPolicy pkh ctx = traceIfFalse "not signed by redeemer pubkeyhash" checkWitness
  where
    info :: TxInfo
    info  = scriptContextTxInfo ctx

    checkWitness :: Bool
    checkWitness  = txSignedBy info pkh
