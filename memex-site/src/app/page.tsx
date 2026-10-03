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
      <Nav />
      <main id="main">
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
