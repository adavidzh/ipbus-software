/*
---------------------------------------------------------------------------

    This file is part of uHAL.

    uHAL is a hardware access library and programming framework
    originally developed for upgrades of the Level-1 trigger of the CMS
    experiment at CERN.

    uHAL is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    uHAL is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with uHAL.  If not, see <http://www.gnu.org/licenses/>.


      Andrew Rose, Imperial College, London
      email: awr01 <AT> imperial.ac.uk

      Marc Magrans de Abril, CERN
      email: marc.magrans.de.abril <AT> cern.ch

---------------------------------------------------------------------------
*/

#include "uhal/HwInterface.hpp"


#include <deque>

#include "uhal/ClientInterface.hpp"
#include "uhal/Node.hpp"


namespace boost {
  template <class Y> class shared_ptr;
}

namespace uhal
{

  HwInterface::HwInterface ( const boost::shared_ptr<ClientInterface>& aClientInterface , const boost::shared_ptr< Node >& aNode ) :
    mClientInterface ( aClientInterface ),
    mNode ( aNode )
  {
    claimNode ( *mNode );
  }


  HwInterface::HwInterface ( const HwInterface& otherHw ) :
    mClientInterface ( otherHw.mClientInterface ),
    mNode ( otherHw.mNode->clone() )
  {
    claimNode ( *mNode );
  }


  HwInterface::~HwInterface()
  {
  }

  void HwInterface::claimNode ( Node& aNode )
  {
    aNode.mHw = this;

    for ( std::deque< Node* >::iterator lIt = aNode.mChildren.begin(); lIt != aNode.mChildren.end(); ++lIt )
    {
      claimNode ( **lIt );
    }
  }

  ClientInterface& HwInterface::getClient()
  {
    return *mClientInterface;
  }

  // void HwInterface::ping()
  // {
  // try
  // {
  // mClientInterface->ping();
  // }
  // catch ( uhal::exception& aExc )
  // {
  // aExc.throw r;
  // }
  // catch ( const std::exception& aExc )
  // {
  // throw // StdException ( aExc );
  // }
  // }

  void HwInterface::dispatch ()
  {
    mClientInterface->dispatch ();
  }


  const std::string& HwInterface::id() const
  {
    return mClientInterface->id();
  }


  std::string HwInterface::uri() const
  {
    return mClientInterface->uri();
  }


  void HwInterface::setTimeoutPeriod ( const uint32_t& aTimeoutPeriod )
  {
    mClientInterface->setTimeoutPeriod ( aTimeoutPeriod );
  }


  uint32_t HwInterface::getTimeoutPeriod()
  {
    return mClientInterface->getTimeoutPeriod();
  }

  const Node& HwInterface::getNode () const
  {
    return *mNode;
  }


  const Node& HwInterface::getNode ( const std::string& aId ) const
  {
    return mNode->getNode ( aId );
  }

  std::vector<std::string> HwInterface::getNodes() const
  {
    return mNode->getNodes();
  }

  std::vector<std::string> HwInterface::getNodes ( const std::string& aRegex ) const
  {
    return mNode->getNodes ( aRegex );
  }

  // ValVector< uint32_t > HwInterface::readReservedAddressInfo ()
  // {
  // try
  // {
  // return mClientInterface->readReservedAddressInfo();
  // }
  // catch ( uhal::exception& aExc )
  // {
  // aExc.rethrowFrom( ThisLocation() );
  // }
  // catch ( const std::exception& aExc )
  // {
  // log ( Error() , "Exception " , Quote ( aExc.what() ) , " caught at " , ThisLocation() );	// uhal::StdException lExc( aExc );
  // lExc.throwFrom( ThisLocation() );
  // }
  // }

}


