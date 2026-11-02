// Captures microphone audio, downsamples to 16 kHz mono and posts 512-sample Int16 frames
// (32 ms) together with an RMS level for the meter.
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.acc = 0;
    this.sum = 0;
    this.count = 0;
    this.out = new Int16Array(512);
    this.pos = 0;
    this.levelSum = 0;
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      // Box-filter decimation: average the input samples that fall into each output sample.
      this.sum += ch[i];
      this.count++;
      this.acc += 1;
      if (this.acc >= this.ratio) {
        this.acc -= this.ratio;
        const v = Math.max(-1, Math.min(1, this.sum / this.count));
        this.sum = 0;
        this.count = 0;
        this.out[this.pos++] = v * 32767;
        this.levelSum += v * v;
        if (this.pos === this.out.length) {
          const level = Math.sqrt(this.levelSum / this.out.length);
          this.port.postMessage({ pcm: this.out.buffer.slice(0), level });
          this.pos = 0;
          this.levelSum = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor("capture-processor", CaptureProcessor);
