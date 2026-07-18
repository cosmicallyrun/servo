interface EngineBindingSmoke {
  constructor(long value);
  attribute long value;
  long add(long rhs);
  long setChild(EngineBindingSmoke child);
  long childValue();
};
