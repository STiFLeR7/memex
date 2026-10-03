import Nav from '@/components/Nav';
import Hero from '@/components/Hero';
import Thesis from '@/components/Thesis';
import ControlPlane from '@/components/ControlPlane';
import Capabilities from '@/components/Capabilities';
import Lifecycle from '@/components/Lifecycle';
import Artifacts from '@/components/Artifacts';
import Final from '@/components/Final';
import Footer from '@/components/Footer';
import RevealEngine from '@/components/RevealEngine';

export default function Page() {
  return (
    <>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <Nav />
      {/* tabIndex -1 so the skip link actually moves focus, not just the
          scroll position: <main> is not focusable on its own. */}
      <main id="main" tabIndex={-1}>
        <Hero />
        <Thesis />
        <ControlPlane />
        <Capabilities />
        <Lifecycle />
        <Artifacts />
        <Final />
      </main>
      <Footer />
      <RevealEngine />
    </>
  );
}
