import "./globals.css";
import { Cormorant_Garamond } from "next/font/google";

const cormorant = Cormorant_Garamond({
  subsets: ["latin"],
  weight: ["300", "400"],
  style: ["normal", "italic"],
  variable: "--font-display",
  display: "swap",
});

export const metadata = {
  title: "NextPick",
  description: "Premium movie and TV recommendation frontend",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" className={cormorant.variable}>
      <head>
        {/* Prevent theme flash before React hydrates */}
        <script dangerouslySetInnerHTML={{ __html: `(function(){try{var t=localStorage.getItem('np-theme')||'dark';document.documentElement.setAttribute('data-theme',t);}catch(e){}})();` }} />
        {/* Favicon */}
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
        <link rel="shortcut icon" href="/favicon.svg" />
        {/* Tabler Icons icon font */}
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/tabler-icons/2.44.0/iconfont/tabler-icons.min.css" />
      </head>
      <body>{children}</body>
    </html>
  );
}
