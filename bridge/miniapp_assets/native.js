// Best-effort native hooks never block the standard web interface.
export function setupNative(telegram) {
  const root=document.documentElement;
  function update() {
    const dark=telegram?.colorScheme==='dark'||(!telegram?.colorScheme&&window.matchMedia?.('(prefers-color-scheme: dark)').matches);
    root.dataset.theme=dark?'dark':'light';
    for(const [key,value] of Object.entries(telegram?.themeParams||{})) {
      if(/^#[0-9a-fA-F]{6}$/.test(value))root.style.setProperty('--tg-theme-'+key.replaceAll('_','-'),value);
    }
    for(const [name,inset] of [['safe',telegram?.safeAreaInset],['content-safe',telegram?.contentSafeAreaInset]]) {
      for(const side of ['top','right','bottom','left']) {
        const amount=Number(inset?.[side]);
        if(Number.isFinite(amount)&&amount>=0)root.style.setProperty('--'+name+'-'+side,Math.min(amount,200)+'px');
      }
    }
  }
  update();
  for(const event of ['themeChanged','safeAreaChanged','contentSafeAreaChanged','viewportChanged']) {
    try{telegram?.onEvent?.(event,update);}catch{}
  }
  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change',update);
}
export function selectionFeedback() {
  try{window.Telegram?.WebApp?.HapticFeedback?.selectionChanged();}catch{}
}
