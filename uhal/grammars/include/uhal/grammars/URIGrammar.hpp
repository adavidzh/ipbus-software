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

#ifndef _uhal_grammars_URIGrammar_hpp_
#define _uhal_grammars_URIGrammar_hpp_



#include <string>
#include <utility>   // for pair
#include <vector>

#include <boost/fusion/adapted/std_pair.hpp>
#include <boost/fusion/adapted/struct/adapt_struct.hpp>
#include <boost/spirit/include/qi_char.hpp>
#include <boost/spirit/include/qi_grammar.hpp>

#include "uhal/grammars/URI.hpp"


// Call to BOOST_FUSION_ADAPT_STRUCT must be at global scope
//! A boost::fusion adaptive struct used by the boost::qi parser
BOOST_FUSION_ADAPT_STRUCT (
  uhal::URI,
  ( std::string , mProtocol )
  ( std::string , mHostname )
  ( std::string , mPort )
  ( std::string , mPath )
  ( std::string , mExtension )
  ( uhal::NameValuePairVectorType, mArguments )
)


namespace grammars
{
  //! A struct wrapping a set of rules as a grammar that can parse a URI of the form "protocol://host:port/patha/pathb/blah.ext?key1=val1&key2=val2&key3=val3"
  struct URIGrammar : boost::spirit::qi::grammar<std::string::const_iterator, uhal::URI(), boost::spirit::ascii::space_type>
  {
    //! Default Constructor where we will define the boost::qi rules relating the members
    URIGrammar();
    //! Boost spirit parsing rule for parsing a URI
    boost::spirit::qi::rule< std::string::const_iterator,	uhal::URI(), 											boost::spirit::ascii::space_type > start;


    boost::spirit::qi::rule< std::string::const_iterator, uhal::URI(),                      boost::spirit::ascii::space_type > EthernetURI;
    boost::spirit::qi::rule< std::string::const_iterator, uhal::URI(),                      boost::spirit::ascii::space_type > PCIeURI;


    //! Boost spirit parsing rule for parsing the "protocol" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::string(),											boost::spirit::ascii::space_type > protocol;
    //! Boost spirit parsing rule for parsing the "hostname" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::string(),											boost::spirit::ascii::space_type > hostname;
    //! Boost spirit parsing rule for parsing the "port" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::string(),											boost::spirit::ascii::space_type > port;
    //! Boost spirit parsing rule for parsing the "path" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::string(),											boost::spirit::ascii::space_type > path;
    //! Boost spirit parsing rule for parsing the "extension" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::string(),											boost::spirit::ascii::space_type > extension;
    //! Boost spirit parsing rule for parsing all of the "key-value pairs" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::vector< std::pair<std::string, std::string> > (),	boost::spirit::ascii::space_type > data_pairs_vector; //NameValuePairVectorType
    //! Boost spirit parsing rule for parsing each of the "key-value pairs" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::pair<std::string, std::string>(),					boost::spirit::ascii::space_type > data_pairs;
    //! Boost spirit parsing rule for parsing the "key" part of the "key-value pairs" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::string(),											boost::spirit::ascii::space_type > data_pairs_1;
    //! Boost spirit parsing rule for parsing the "value" part of the "key-value pairs" part of a URI
    boost::spirit::qi::rule< std::string::const_iterator,	std::string(),											boost::spirit::ascii::space_type > data_pairs_2;


    boost::spirit::qi::rule< std::string::const_iterator, std::string(),                      boost::spirit::ascii::space_type > empty_string;    
  };
}

#endif
