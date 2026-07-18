function tlv_probe(value) {
  return (value + 1) | 0;
}

%PrepareFunctionForOptimization(tlv_probe);
for (let i = 0; i < 20_000; ++i) {
  tlv_probe(i);
}

%OptimizeFunctionOnNextCall(tlv_probe);
if (tlv_probe(41) !== 42) {
  throw new Error("TurboLev probe returned the wrong result");
}
