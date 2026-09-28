// Original local line icons. No icon font, third-party asset or network request.
const paths = {
  dashboard:'M3 10 12 3l9 7M5 9v11h5v-6h4v6h5V9',
  characters:'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  sessions:'M4 4h16v12H9l-5 4V4M8 8h8M8 12h5',
  usage:'M4 20V10M10 20V4M16 20v-8M22 20H2',
  more:'M5 5h4v4H5zM15 5h4v4h-4zM5 15h4v4H5zM15 15h4v4h-4z',
  models:'M9 3h6v4l4 2v6l-4 2v4H9v-4l-4-2V9l4-2V3M9 10h6v4H9zM1 10h4M19 10h4M1 14h4M19 14h4',
  memory:'M12 4c-4-3-8 0-7 4-4 1-4 7 0 8-1 4 4 6 7 3V4M12 4c4-3 8 0 7 4 4 1 4 7 0 8 1 4-4 6-7 3M8 9l4 3M16 8l-4 5M7 16l5-2',
  personas:'M20 21a8 8 0 0 0-16 0M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  worlds:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',
  databank:'M4 7h16v13H4zM4 7V4h6l2 3M8 11h8M8 15h5',
  system:'M3 5h18v12H3zM8 21h8M12 17v4M6 11h3l2-3 3 6 2-3h2',
  refresh:'M20 8a8 8 0 0 0-13-4L3 8M3 3v5h5M4 16a8 8 0 0 0 13 4l4-4M21 21v-5h-5',
  arrow:'M5 12h14M13 6l6 6-6 6',
  close:'m6 6 12 12M6 18 18 6',
  search:'M10 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16M16 16l6 6',
  shield:'M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3M8 12l3 3 5-6',
  spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3',
};
export function icon(name) {
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
  for(const [key,value] of Object.entries({viewBox:'0 0 24 24',width:'22',height:'22',fill:'none',stroke:'currentColor','stroke-width':'1.7','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true',focusable:'false',class:'ui-icon'}))svg.setAttribute(key,value);
  const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',paths[name]||paths.spark);svg.append(path);return svg;
}
