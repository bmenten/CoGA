import React from 'react';

interface Props {
  title: string;
  id?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}

const CollapsibleSection: React.FC<Props> = ({
  title,
  id,
  defaultOpen = true,
  children,
}) => {
  const [open, setOpen] = React.useState(defaultOpen);

  React.useEffect(() => {
    const handleHashChange = () => {
      if (id && window.location.hash === `#${id}`) {
        setOpen(true);
      }
    };
    handleHashChange();
    window.addEventListener('hashchange', handleHashChange);
    return () => window.removeEventListener('hashchange', handleHashChange);
  }, [id]);
  return (
    <section id={id} className="space-y-4">
      <h2
        className="text-xl font-semibold cursor-pointer"
        onClick={() => setOpen((o) => !o)}
      >
        {title}
      </h2>
      {open && <div>{children}</div>}
    </section>
  );
};

export default CollapsibleSection;
