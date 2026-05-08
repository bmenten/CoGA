import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import PageState from '../components/PageState';

const NotFound: React.FC = () => {
  const location = useLocation();
  return (
    <PageState
      kicker="Not Found"
      title="Page not found"
      message={`No route matches ${location.pathname}.`}
      narrow
      action={
        <>
          <Link to="/dashboard" className="form-button hover:no-underline">
            Go to Dashboard
          </Link>
          <button onClick={() => window.history.back()} className="button-ghost">
            Go back
          </button>
        </>
      }
    />
  );
};

export default NotFound;
