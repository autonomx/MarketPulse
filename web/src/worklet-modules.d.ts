// Vite bundles `?worker&url` imports (audio worklet processors) and returns the emitted file's URL.
declare module '*?worker&url' {
  const url: string;
  export default url;
}
