class PcmRecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.active = true;
    this.port.onmessage = (event) => { this.active = event.data === 'resume'; };
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && this.active) {
      const copy = new Float32Array(channel);
      this.port.postMessage(copy, [copy.buffer]);
    }
    return true;
  }
}
registerProcessor('pcm-recorder', PcmRecorderProcessor);
